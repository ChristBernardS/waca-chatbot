"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     WACA — UKDW's Personal Chatbot System                    ║
║                            rate_limiter.py  (v1)                             ║
║                   Daily Question Limit per User (IP-based)                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

ARSITEKTUR RATE LIMITER — DUA LAPIS:

    ┌─────────────────────────────────────────────────────────────────────┐
    │  Lapisan 1 (Primary)  — PostgreSQL                                  │
    │    Tabel: rate_limits (identifier, tanggal, jumlah)                 │
    │    Selalu aktif. Tidak butuh konfigurasi tambahan.                  │
    │    Reset: otomatis karena key menyertakan tanggal hari ini.         │
    ├─────────────────────────────────────────────────────────────────────┤
    │  Lapisan 2 (Optional) — Cloudflare Workers KV                       │
    │    Aktif jika semua variabel CF_* di .env terisi.                   │
    │    Berguna sebagai edge-level cache agar request berlebih tidak     │
    │    sampai ke server sama sekali (jika WACA berada di belakang CF).  │
    │    Jika CF tidak tersedia, sistem tetap berjalan via PostgreSQL.    │
    └─────────────────────────────────────────────────────────────────────┘

CARA IDENTIFIKASI USER (tanpa autentikasi):

    1. CF-Connecting-IP  — diisi Cloudflare dengan IP asli client.
       Paling akurat karena tidak bisa di-spoof saat traffic lewat CF.
    2. X-Forwarded-For   — diisi proxy/load balancer (ambil IP pertama).
    3. request.client.host — fallback langsung dari koneksi TCP.

    ⚠️  Keterbatasan IP-based:
        Di jaringan kampus dengan NAT, banyak user bisa berbagi IP yang sama.
        Solusi jangka panjang: autentikasi SSO kampus, lalu gunakan NIM
        sebagai identifier. Untuk saat ini, IP adalah pendekatan paling
        praktis tanpa sistem login.

KONFIGURASI (.env):

    DAILY_QUESTION_LIMIT=20        # Jumlah pertanyaan per user per hari

    # Cloudflare Workers KV (opsional, isi semua atau kosongkan semua)
    CF_ACCOUNT_ID=xxxxxxxxxxxx     # Account ID di Cloudflare Dashboard
    CF_KV_NAMESPACE_ID=xxxxxxxxxx  # Namespace ID di Workers → KV
    CF_API_TOKEN=xxxxxxxxxxxxxxxxx # API Token dengan izin KV:Edit

CARA KERJA CLOUDFLARE KV:

    Key   : "waca:rl:{ip}:{YYYY-MM-DD}"
    Value : jumlah pertanyaan (integer, disimpan sebagai string)
    TTL   : 86400 detik (1 hari) — KV otomatis hapus setelah expired

    Flow  : check_rate_limit()
              ├─ cek KV dulu (cepat, edge)
              │    └─ jika count >= limit → tolak TANPA baca PostgreSQL
              └─ jika KV miss → cek PostgreSQL → sinkron balik ke KV
"""

import logging
import os
from datetime import date, datetime, timezone, timedelta
from typing import Optional, Tuple

import httpx
from fastapi import Request

from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

from dataset.database import get_pool

logger = logging.getLogger(__name__)

DAILY_LIMIT: int = int(os.getenv("DAILY_QUESTION_LIMIT"))

CF_ACCOUNT_ID    = os.getenv("CF_ACCOUNT_ID", "").strip()
CF_KV_NAMESPACE  = os.getenv("CF_KV_NAMESPACE_ID", "").strip()
CF_API_TOKEN     = os.getenv("CF_API_TOKEN", "").strip()

CF_ENABLED: bool = bool(CF_ACCOUNT_ID and CF_KV_NAMESPACE and CF_API_TOKEN)

if CF_ENABLED:
    logger.info("✅ Cloudflare KV rate limiter aktif.")
else:
    logger.info("ℹ️  Cloudflare KV tidak dikonfigurasi — rate limiter hanya menggunakan PostgreSQL.")

_WIB = timezone(timedelta(hours=7))



def get_client_ip(request: Request) -> str:
    """
    Ambil IP address client dengan urutan prioritas:
      1. CF-Connecting-IP  (Cloudflare, paling terpercaya jika pakai CF proxy)
      2. X-Forwarded-For   (proxy/load balancer umum, ambil IP pertama)
      3. request.client.host (koneksi TCP langsung, fallback)
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()

    if request.client:
        return request.client.host

    return "unknown"



def _cf_key(ip: str, today: date) -> str:
    """Format key KV: waca:rl:{ip}:{YYYY-MM-DD}"""
    return f"waca:rl:{ip}:{today.isoformat()}"

def _cf_base_url() -> str:
    return (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE}/values"
    )

def _cf_headers() -> dict:
    return {"Authorization": f"Bearer {CF_API_TOKEN}"}


async def _cf_get(ip: str, today: date) -> Optional[int]:
    """
    Baca jumlah pertanyaan dari Cloudflare KV.
    Return None jika key tidak ada atau CF tidak tersedia.
    """
    if not CF_ENABLED:
        return None
    key = _cf_key(ip, today)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{_cf_base_url()}/{key}", headers=_cf_headers())
            if r.status_code == 200:
                return int(r.text)
            return None
    except Exception as e:
        logger.warning(f"[CF-KV] Gagal membaca key '{key}': {e}")
        return None


async def _cf_set(ip: str, today: date, count: int) -> None:
    """
    Tulis/perbarui jumlah pertanyaan di Cloudflare KV.
    TTL 86400 detik = 1 hari (KV akan otomatis hapus key setelah itu).
    """
    if not CF_ENABLED:
        return
    key = _cf_key(ip, today)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.put(
                f"{_cf_base_url()}/{key}",
                headers={**_cf_headers(), "Content-Type": "text/plain"},
                params={"expiration_ttl": 86400},
                content=str(count),
            )
    except Exception as e:
        logger.warning(f"[CF-KV] Gagal menulis key '{key}': {e}")



async def _pg_get_count(conn, identifier: str, today: date) -> int:
    """Ambil jumlah pertanyaan user hari ini dari PostgreSQL."""
    row = await conn.fetchrow(
        "SELECT jumlah FROM rate_limits WHERE identifier = $1 AND tanggal = $2",
        identifier, today
    )
    return row["jumlah"] if row else 0


async def _pg_increment(conn, identifier: str, today: date) -> int:
    """
    Tambah hitungan pertanyaan untuk user hari ini.
    Menggunakan INSERT ON CONFLICT DO UPDATE untuk upsert atomik.
    Mengembalikan jumlah SETELAH increment.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO rate_limits (identifier, tanggal, jumlah)
        VALUES ($1, $2, 1)
        ON CONFLICT (identifier, tanggal)
        DO UPDATE SET jumlah = rate_limits.jumlah + 1
        RETURNING jumlah
        """,
        identifier, today
    )
    return row["jumlah"]



class RateLimitResult:
    """
    Hasil pengecekan rate limit, dikembalikan ke endpoint /chat.

    Atribut:
        allowed     (bool)  — True jika user masih boleh bertanya
        count       (int)   — Jumlah pertanyaan yang sudah dilakukan hari ini
                              (SEBELUM pertanyaan saat ini jika ditolak,
                               SETELAH increment jika diizinkan)
        remaining   (int)   — Sisa pertanyaan hari ini
        limit       (int)   — Batas harian yang dikonfigurasi
        reset_at    (str)   — Waktu reset dalam format "HH:MM WIB"
    """
    def __init__(self, allowed: bool, count: int, limit: int):
        self.allowed   = allowed
        self.count     = count
        self.limit     = limit
        self.remaining = max(0, limit - count)
        tomorrow_wib   = datetime.now(_WIB).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        self.reset_at  = tomorrow_wib.strftime("%d %b %Y, 00:00 WIB")

    def to_dict(self) -> dict:
        return {
            "allowed":   self.allowed,
            "count":     self.count,
            "remaining": self.remaining,
            "limit":     self.limit,
            "reset_at":  self.reset_at,
        }


async def check_and_increment(request: Request) -> RateLimitResult:
    """
    Fungsi utama rate limiter. Dipanggil di awal setiap POST /chat.

    Alur:
        1. Identifikasi user via IP.
        2. Cek Cloudflare KV terlebih dahulu (lebih cepat, opsional).
           → Jika KV sudah mencatat count >= limit: tolak langsung.
        3. Jika KV tidak tersedia atau count < limit: cek PostgreSQL.
        4. Jika count masih di bawah limit:
           a. Increment di PostgreSQL (atomik).
           b. Sinkronkan nilai baru ke Cloudflare KV.
           c. Return allowed=True.
        5. Jika count sudah >= limit: return allowed=False.
    """
    ip    = get_client_ip(request)
    today = datetime.now(_WIB).date()

    cf_count = await _cf_get(ip, today)
    if cf_count is not None and cf_count >= DAILY_LIMIT:
        logger.info(f"[RATE] {ip} ditolak via CF-KV (count={cf_count}, limit={DAILY_LIMIT})")
        return RateLimitResult(allowed=False, count=cf_count, limit=DAILY_LIMIT)

    pool = await get_pool()
    async with pool.acquire() as conn:
        current = await _pg_get_count(conn, ip, today)

        if current >= DAILY_LIMIT:
            logger.info(f"[RATE] {ip} ditolak via PG (count={current}, limit={DAILY_LIMIT})")
            await _cf_set(ip, today, current)
            return RateLimitResult(allowed=False, count=current, limit=DAILY_LIMIT)

        new_count = await _pg_increment(conn, ip, today)

    await _cf_set(ip, today, new_count)

    logger.info(f"[RATE] {ip} diizinkan (count={new_count}/{DAILY_LIMIT})")
    return RateLimitResult(allowed=True, count=new_count, limit=DAILY_LIMIT)


async def get_status(request: Request) -> RateLimitResult:
    """
    Cek status rate limit tanpa increment.
    Digunakan oleh endpoint GET /rate-limit-status untuk update UI.
    """
    ip    = get_client_ip(request)
    today = datetime.now(_WIB).date()

    cf_count = await _cf_get(ip, today)
    if cf_count is not None:
        return RateLimitResult(
            allowed=cf_count < DAILY_LIMIT,
            count=cf_count,
            limit=DAILY_LIMIT
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        current = await _pg_get_count(conn, ip, today)

    return RateLimitResult(
        allowed=current < DAILY_LIMIT,
        count=current,
        limit=DAILY_LIMIT
    )
