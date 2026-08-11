"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     WACA — UKDW's Personal Chatbot System                    ║
║                         response_layer.py  (v3)                              ║
║                   Stage 3 — Response Generation Layer                        ║
╚══════════════════════════════════════════════════════════════════════════════╝

Perubahan v3:
  - Kolom 'nominal' di beasiswa diganti 'cakupan' (lebih deskriptif)
  - Kolom 'kategori' di beasiswa baru: mahasiswa_baru/aktif/eksternal/pinjaman
  - Kolom 'jenis_program' di pertukaran_mahasiswa: student_exchange/short_term/iisma
  - Formatter fallback diperbarui untuk semua kolom baru
  - System prompt diperbarui menyebutkan sumber data lengkap
"""

import json
import logging
import re
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


RESPONSE_SYSTEM_PROMPT = """Kamu adalah Waca, asisten chatbot resmi UKDW (Universitas Kristen Duta Wacana) yang ramah dan informatif.

Tugasmu membantu civitas akademika UKDW — mahasiswa aktif, calon mahasiswa, alumni, dosen, dan mitra — dengan informasi tentang:
- Layanan akademik (Biro 1): transkrip nilai, KRS, cuti, KTM, presensi, surat keterangan, wisuda, yudisium
- Kemahasiswaan & karir (Biro 3): beasiswa, asuransi, organisasi, PKM, job fair, pinjaman, tracer study
- Kerjasama & relasi publik (Biro 4): MoU, kerjasama perusahaan, iklan, baliho, videotron
- Student Exchange & short-term program: exchange di Korea (HGU, KMOU), Taiwan (I-Shou, Tunghai), USA (Goshen, Ohio, Ouachita), Filipina (PNU); short-term di India, program ACUCA; IISMA
- Penerimaan mahasiswa baru (PMB): 6 jalur seleksi (Prestasi, Mandiri, SKL, UTBK, Kedokteran, Filsafat Keilahian), syarat, jadwal, biaya daftar Rp 200.000
- Biaya kuliah 2025/2026: DPFP, kelas Introduction to College English (ICE) sebanyak 3 level, SPP Tetap, SPP Variabel per prodi
- Program studi S1 & S2: 11 prodi S1 + 2 prodi S2, akreditasi, deskripsi
- Beasiswa: UKDW Scholarship, Talenta, Samapta, KIP-Kuliah, ADiK, Kebutuhan, BPD DIY, Prestasi Akademik, Prestasi Umum, Poin SAC, Anak Karyawan, ADARO, Scranton, Gereja, Djarum, LPDP, Pinjaman Registrasi

PANDUAN MENJAWAB:
1. Jawab dalam Bahasa Indonesia yang jelas, hangat, dan profesional.
2. Dasarkan jawaban HANYA pada data dari database UKDW yang disediakan. Jangan mengarang informasi.
3. Format rapi: gunakan bullet point untuk daftar, **tebal** untuk angka dan informasi penting, heading untuk kategorisasi.
4. Selalu sertakan langkah konkret dan kontak yang relevan jika tersedia dalam data.
5. Jika data tidak memadai, informasikan jujur dan arahkan ke kontak: pmb@ukdw.ac.id / beasiswa@ukdw.ac.id / kerjasama@staff.ukdw.ac.id / pmb.ukdw.ac.id.
6. Sertakan website/link resmi UKDW jika tersedia dalam data.
7. Perkenalkan dirimu sebagai Waca jika pengguna bertanya siapa kamu.
8. JANGAN gunakan notasi LaTeX seperti $\rightarrow$, $\times$, $\leq$, \textbf{}, atau format LaTeX lainnya. Gunakan simbol Unicode langsung (→, ×, ≤, ≥, ≠, dll.) atau format Markdown (**tebal**, *miring*). UI chat tidak memiliki renderer LaTeX.

PENTING — Dua kondisi berbeda yang HARUS dibedakan cara menjawabnya:

[A] Pertanyaan TENTANG UKDW tapi data tidak ditemukan di database:
    → Sampaikan bahwa informasi belum tersedia di sistem, lalu arahkan ke kontak terkait.
    → Contoh: "Maaf, informasi tentang [topik] belum tersedia di sistem saya saat ini.
       Silakan hubungi [unit] di [kontak] untuk informasi lebih lanjut."

[B] Pertanyaan SAMA SEKALI DI LUAR lingkup UKDW (pengetahuan umum, tokoh publik,
    berita, sains umum, dll.) — ditandai dengan [TOPIK DI LUAR CAKUPAN UKDW]:
    → Akui bahwa pertanyaan tersebut di luar cakupanmu sebagai chatbot UKDW.
    → Jawab singkat jika kamu tahu (1-2 kalimat saja), lalu tawarkan bantuan kembali
       ke topik UKDW dengan ramah. JANGAN arahkan ke kontak UKDW untuk hal ini.
    → Contoh: "Waca adalah asisten khusus UKDW, jadi [topik] bukan bidang saya. 
       Tapi secara singkat, [jawaban 1-2 kalimat]. Ada yang bisa saya bantu 
       seputar UKDW?"
"""


def _format_data_for_prompt(data: list, intent: str) -> str:
    """
    Ubah baris database menjadi teks terstruktur untuk LLM.
    Handles kolom baru v3: cakupan, kategori (beasiswa), jenis_program (exchange).
    """
    if not data:
        return "Tidak ada data yang ditemukan di database UKDW untuk pertanyaan ini."

    lines = [f"Ditemukan {len(data)} data dari database UKDW:\n"]
    for i, row in enumerate(data, 1):
        lines.append(f"Data {i}:")
        for key, value in row.items():
            if value is None:
                continue
            if isinstance(value, int) and key in (
                "dpfp", "ice_per_level", "spp_tetap_per_semester", "spp_variabel_per_sks", "biaya_daftar"
            ):
                value = f"Rp {value:,.0f}".replace(",", ".")
            lines.append(f"  {key}: {value}")
        lines.append("")
    return "\n".join(lines)


async def generate_response(
    user_message:   str,
    intent:         str,
    entities:       dict,
    retrieved_data: list,
    history:        list = []
) -> str:
    """Hasilkan jawaban natural language dari data yang diambil Stage 2."""
    data_context = _format_data_for_prompt(retrieved_data, intent)

    messages = [{"role": "system", "content": RESPONSE_SYSTEM_PROMPT}]
    for turn in (history or [])[-4:]:
        messages.append({
            "role":    turn.get("role", "user"),
            "content": turn.get("content", "")
        })


    is_off_topic   = (intent == "general" and not retrieved_data)
    is_ukdw_no_data = (intent != "general" and not retrieved_data)

    if is_off_topic:
        topic_signal = (
            "[TOPIK DI LUAR CAKUPAN UKDW]\n"
            "Pertanyaan ini tidak berkaitan dengan informasi UKDW. "
            "Jawab singkat (1-2 kalimat) jika kamu tahu, lalu tawarkan "
            "bantuan kembali ke topik UKDW. JANGAN arahkan ke kontak UKDW."
        )
    elif is_ukdw_no_data:
        topic_signal = (
            "[DATA UKDW BELUM TERSEDIA]\n"
            "Pertanyaan ini relevan dengan UKDW, namun data belum ada di "
            "database. Sampaikan jujur dan arahkan ke kontak terkait di UKDW."
        )
    else:
        topic_signal = "Jawab berdasarkan data UKDW di bawah ini."

    user_prompt = (
        f"Pertanyaan pengguna: {user_message}\n\n"
        f"Intent terdeteksi: {intent}\n"
        f"Entitas diekstrak: {json.dumps(entities, ensure_ascii=False)}\n\n"
        f"Konteks: {topic_signal}\n\n"
        f"Hasil dari database UKDW:\n{data_context}\n\n"
        "Berikan jawaban yang sesuai konteks di atas."
    )
    messages.append({"role": "user", "content": user_prompt})

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model    = os.getenv("OLLAMA_MODEL",    "llama3.2")
    api_key  = os.getenv("OLLAMA_API_KEY",  "")
    req_hdrs = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    payload = {
        "model":    model,
        "messages": messages,
        "stream":   False,
        "think":    False,
        "options":  {"temperature": 0.7, "top_p": 0.9, "num_predict": 1024}
    }

    try:
        async with httpx.AsyncClient(timeout=60.0, headers=req_hdrs) as client:
            response = await client.post(f"{base_url}/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()

        msg   = result.get("message", {})
        reply = (msg.get("content") or "").strip()

        if not reply:
            logger.warning("[RES] message.content kosong — model mungkin hanya menghasilkan thinking. Gunakan template fallback.")
            return _fallback_response(intent, retrieved_data)

        logger.debug(f"[RES] Preview: {reply[:200]}…")
        return reply

    except httpx.ConnectError:
        logger.warning("[RES] Ollama tidak dapat dihubungi. Gunakan template fallback.")
        return _fallback_response(intent, retrieved_data)
    except httpx.TimeoutException:
        logger.warning("[RES] Ollama timeout. Gunakan template fallback.")
        return _fallback_response(intent, retrieved_data)
    except Exception as e:
        logger.error(f"[RES] Error: {e}")
        return _fallback_response(intent, retrieved_data)


async def generate_response_stream(
    user_message:   str,
    intent:         str,
    entities:       dict,
    retrieved_data: list,
    history:        list = []
):
    """
    Versi streaming dari generate_response().

    Memanggil Ollama dengan stream=True sehingga token mengalir langsung
    ke caller tanpa menunggu seluruh respons selesai dibuat.

    Yields:
        str — potongan teks (token/sub-kata) dari LLM, satu per satu.

    Fallback:
        Jika Ollama tidak dapat dihubungi atau timeout, yield kata per kata
        dari _fallback_response() agar caller tetap menerima teks yang valid.

    Cara kerja internal:
        1. Bangun prompt yang sama persis dengan generate_response().
        2. POST ke /api/chat dengan "stream": True.
        3. Baca respons baris per baris (NDJSON) menggunakan aiter_lines().
        4. Setiap baris berisi {"message":{"content":"<token>"},"done":false}.
        5. Yield message.content selama done=false; stop saat done=true.
    """
    data_context = _format_data_for_prompt(retrieved_data, intent)

    messages = [{"role": "system", "content": RESPONSE_SYSTEM_PROMPT}]
    for turn in (history or [])[-4:]:
        messages.append({
            "role":    turn.get("role", "user"),
            "content": turn.get("content", "")
        })

    is_off_topic    = (intent == "general" and not retrieved_data)
    is_ukdw_no_data = (intent != "general" and not retrieved_data)

    if is_off_topic:
        topic_signal = (
            "[TOPIK DI LUAR CAKUPAN UKDW]\n"
            "Pertanyaan ini tidak berkaitan dengan informasi UKDW. "
            "Jawab singkat (1-2 kalimat) jika kamu tahu, lalu tawarkan "
            "bantuan kembali ke topik UKDW. JANGAN arahkan ke kontak UKDW."
        )
    elif is_ukdw_no_data:
        topic_signal = (
            "[DATA UKDW BELUM TERSEDIA]\n"
            "Pertanyaan ini relevan dengan UKDW, namun data belum ada di "
            "database. Sampaikan jujur dan arahkan ke kontak terkait di UKDW."
        )
    else:
        topic_signal = "Jawab berdasarkan data UKDW di bawah ini."

    user_prompt = (
        f"Pertanyaan pengguna: {user_message}\n\n"
        f"Intent terdeteksi: {intent}\n"
        f"Entitas diekstrak: {json.dumps(entities, ensure_ascii=False)}\n\n"
        f"Konteks: {topic_signal}\n\n"
        f"Hasil dari database UKDW:\n{data_context}\n\n"
        "Berikan jawaban yang sesuai konteks di atas."
    )
    messages.append({"role": "user", "content": user_prompt})

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model    = os.getenv("OLLAMA_MODEL",    "llama3.2")
    api_key  = os.getenv("OLLAMA_API_KEY",  "")
    req_hdrs = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    payload = {
        "model":    model,
        "messages": messages,
        "stream":   True,
        "think":    False,
        "options":  {
            "temperature": 0.7, 
            "top_p": 0.9, 
            "num_predict": 1024
            }
    }

    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=req_hdrs) as client:
            async with client.stream("POST", f"{base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                accumulated = ""

                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        accumulated += token
                        yield token

                    if chunk.get("done", False):
                        break

                if not accumulated:
                    logger.warning(
                        "[RES_STREAM] Tidak ada token dari LLM — "
                        "kemungkinan model mengembalikan thinking saja. "
                        "Gunakan fallback."
                    )
                    fallback = _fallback_response(intent, retrieved_data)
                    for word in fallback.split(" "):
                        yield word + " "

    except httpx.ConnectError:
        logger.warning("[RES_STREAM] Ollama tidak dapat dihubungi — fallback template.")
        fallback = _fallback_response(intent, retrieved_data)
        for word in fallback.split(" "):
            yield word + " "

    except httpx.TimeoutException:
        logger.warning("[RES_STREAM] Ollama timeout — fallback template.")
        fallback = _fallback_response(intent, retrieved_data)
        for word in fallback.split(" "):
            yield word + " "

    except Exception as e:
        logger.error(f"[RES_STREAM] Error tidak terduga: {e}")
        fallback = _fallback_response(intent, retrieved_data)
        for word in fallback.split(" "):
            yield word + " "



def _fallback_response(intent: str, data: list) -> str:
    """
    Template Python murni ketika Ollama tidak tersedia (ConnectError/Timeout/empty content).

    Tiga kondisi berbeda menghasilkan pesan yang berbeda:
      1. intent=general, data kosong → pertanyaan off-topic (di luar UKDW)
      2. intent=UKDW, data kosong   → topik UKDW tapi data belum ada di DB
      3. data ada                   → format data sesuai intent (happy path fallback)
    """
    if intent == "general" and not data:
        return (
            "Halo! Saya Waca, asisten chatbot khusus UKDW. 😊\n\n"
            "Sepertinya pertanyaan Anda berada di luar cakupan saya sebagai "
            "asisten informasi UKDW. Saya dirancang untuk membantu hal-hal "
            "seputar kampus, seperti:\n"
            "  • Pendaftaran & penerimaan mahasiswa baru\n"
            "  • Biaya kuliah & beasiswa\n"
            "  • Program studi & akademik\n"
            "  • Student exchange & program internasional\n"
            "  • Layanan kemahasiswaan\n\n"
            "Ada yang bisa saya bantu seputar UKDW?"
        )

    if not data:
        return (
            "Maaf, saya tidak menemukan informasi yang sesuai di database UKDW "
            "untuk pertanyaan Anda.\n\n"
            "Silakan hubungi unit terkait untuk informasi lebih lengkap:\n"
            "  • **PMB** (pendaftaran & biaya): pmb@ukdw.ac.id | 0813 9160 7395\n"
            "  • **Biro 1** (layanan akademik): WA 081392521604\n"
            "  • **Biro 3** (kemahasiswaan & beasiswa): beasiswa@ukdw.ac.id\n"
            "  • **Biro 4** (kerjasama & exchange): kerjasama@staff.ukdw.ac.id\n"
            "  • **Website resmi**: pmb.ukdw.ac.id | ukdw.ac.id"
        )

    templates = {
        "layanan_akademik":  _fmt_pengetahuan,
        "kemahasiswaan":     _fmt_pengetahuan,
        "kerjasama":         _fmt_pengetahuan,
        "student_exchange":  _fmt_student_exchange,
        "pendaftaran":       _fmt_pendaftaran,
        "biaya_kuliah":      _fmt_biaya_kuliah,
        "program_studi":     _fmt_program_studi,
        "beasiswa":          _fmt_beasiswa,
        "general":           _fmt_pengetahuan,
    }
    return templates.get(intent, _fmt_pengetahuan)(data)



def _fmt_pengetahuan(data: list) -> str:
    """Format hasil Q&A dari tabel pengetahuan."""
    lines = []
    for item in data[:4]:
        if "jawaban" not in item:
            continue
        if item.get("pertanyaan"):
            lines.append(f"**{item['pertanyaan']}**")
        lines.append(item["jawaban"])
        lines.append("")
    return "\n".join(lines).strip() or "Informasi tidak ditemukan. Hubungi unit terkait di UKDW."


def _fmt_biaya_kuliah(data: list) -> str:
    lines = ["**Rincian Biaya Kuliah UKDW 2025/2026:**\n"]
    bk = [d for d in data if "dpfp" in d]
    for b in bk[:8]:
        lines.append(f"**{b.get('nama_prodi', '')} ({b.get('jenjang', 'S1')})**")
        if b.get("dpfp") is not None:
            lines.append(f"  • DPFP (sekali masuk): **Rp {b['dpfp']:,.0f}**".replace(",", "."))
        if b.get("ice_per_level") is not None:
            lines.append(f"  • ICE (per level, total ada 3 level): **Rp {b['ice_per_level']:,.0f}**".replace(",", "."))
        if b.get("spp_tetap_per_semester") is not None:
            lines.append(f"  • SPP Tetap/semester: **Rp {b['spp_tetap_per_semester']:,.0f}**".replace(",", "."))
        if b.get("spp_variabel_per_sks") is not None:
            lines.append(f"  • SPP Variabel/SKS: **Rp {b['spp_variabel_per_sks']:,.0f}**".replace(",", "."))
        if b.get("catatan"):
            lines.append(f"  • Catatan: {b['catatan']}")
        lines.append("")
    lines.append("ℹ️ Info lengkap: **pmb.ukdw.ac.id**")
    return "\n".join(lines)


def _fmt_pendaftaran(data: list) -> str:
    lines = ["**Informasi Pendaftaran Mahasiswa Baru UKDW:**\n"]

    jalur_data = [d for d in data if "syarat_utama" in d]
    if jalur_data:
        for j in jalur_data[:3]:
            lines.append(f"**{j.get('nama_jalur', '')}**")
            if j.get("berlaku_untuk"):
                lines.append(f"  • Berlaku untuk: {j['berlaku_untuk']}")
            if j.get("syarat_utama"):
                syarat = j["syarat_utama"][:250]
                lines.append(f"  • Syarat: {syarat}")
            if j.get("biaya_daftar") is not None:
                lines.append(f"  • Biaya daftar: **Rp {j['biaya_daftar']:,.0f}**".replace(",", "."))
            if j.get("catatan_khusus"):
                lines.append(f"  • Catatan: {j['catatan_khusus']}")
            if j.get("website"):
                lines.append(f"  • Info lengkap: {j['website']}")
            lines.append("")

    jadwal_data = [d for d in data if "gelombang" in d]
    if jadwal_data:
        lines.append("**Jadwal Seleksi:**")
        for jd in jadwal_data[:6]:
            jalur = jd.get("nama_jalur", "")
            gel   = jd.get("gelombang", "")
            buka  = jd.get("tanggal_buka", "-")
            pengumuman = jd.get("tanggal_pengumuman", "-")
            ujian = jd.get("tanggal_ujian", "")
            tes_info = f" (Tes: {ujian})" if ujian and ujian not in ("-", "Tanpa Tes") else ""
            lines.append(f"  • {jalur} {gel}: {buka}{tes_info} → Pengumuman: {pengumuman}")
        lines.append("")

    qa_data = [d for d in data if "jawaban" in d and "gelombang" not in d and "syarat_utama" not in d]
    for qa in qa_data[:2]:
        if qa.get("jawaban"):
            lines.append(qa["jawaban"])
            lines.append("")

    lines.append("ℹ️ Pendaftaran & info lengkap: **pmb.ukdw.ac.id** | WA: 0813 9160 7395")
    return "\n".join(lines)


def _fmt_student_exchange(data: list) -> str:
    lines = ["**Program Pertukaran Mahasiswa UKDW (OIA):**\n"]

    prog_data = [d for d in data if "universitas_mitra" in d]

    exchange = [p for p in prog_data if p.get("jenis_program") == "student_exchange"]
    short    = [p for p in prog_data if p.get("jenis_program") == "short_term"]

    if exchange:
        lines.append("**🎓 Student Exchange (Credit Transfer):**")
        for p in exchange[:5]:
            lines.append(f"\n**{p.get('nama_program', '')}**")
            lines.append(f"  • Universitas: {p.get('universitas_mitra', '-')}, {p.get('negara', '-')}")
            if p.get("durasi"):
                lines.append(f"  • Durasi: {p['durasi']}")
            if p.get("pendanaan"):
                lines.append(f"  • Pendanaan: {p['pendanaan']}")
            if p.get("kontak"):
                lines.append(f"  • Kontak: {p['kontak']}")

    if short:
        lines.append(f"\n**🏕️ Short-Term Programs ({len(short)} program):**")
        for p in short[:3]:
            lines.append(
                f"  • {p.get('nama_program', '-')} — {p.get('universitas_mitra', '-')}, {p.get('negara', '-')}"
            )

    qa_data = [d for d in data if "jawaban" in d and "universitas_mitra" not in d]
    if qa_data:
        lines.append("")
        for qa in qa_data[:2]:
            lines.append(qa.get("jawaban", ""))
            lines.append("")

    lines.append("\nℹ️ Daftar lengkap universitas mitra: **www.ukdw.ac.id/en/oia/**")
    lines.append("📋 Ajukan lewat: **bit.ly/appOutboundUKDW** | WA OIA: **bit.ly/askOIA**")
    return "\n".join(lines)


def _fmt_program_studi(data: list) -> str:
    lines = ["**Program Studi UKDW:**\n"]
    for p in data[:10]:
        if "nama_prodi" not in p:
            continue
        akr = p.get("akreditasi", "-")
        lines.append(
            f"  • **{p.get('nama_prodi', '-')} ({p.get('jenjang', '-')})**"
            f" — {p.get('fakultas', '-')} | Akreditasi: {akr}"
        )
    lines.append("\nℹ️ Info prodi lengkap: **ukdw.ac.id**")
    return "\n".join(lines)


def _fmt_beasiswa(data: list) -> str:
    lines = ["**Beasiswa yang Tersedia di UKDW:**\n"]

    bsw_data = [d for d in data if "nama_beasiswa" in d]

    kategori_urutan = ["mahasiswa_baru", "mahasiswa_aktif", "eksternal", "pinjaman"]
    label_kat = {
        "mahasiswa_baru":   "🎓 Beasiswa untuk Calon Mahasiswa Baru",
        "mahasiswa_aktif":  "📚 Beasiswa untuk Mahasiswa Aktif",
        "eksternal":        "🏛️ Beasiswa Eksternal (Pemerintah & Lembaga)",
        "pinjaman":         "🤝 Pinjaman Registrasi (Tanpa Bunga)"
    }

    grouped: dict[str, list] = {k: [] for k in kategori_urutan}
    for b in bsw_data:
        kat = b.get("kategori", "mahasiswa_aktif")
        if kat in grouped:
            grouped[kat].append(b)

    printed = 0
    for kat in kategori_urutan:
        items = grouped[kat]
        if not items:
            continue
        lines.append(f"\n**{label_kat.get(kat, kat)}:**")
        for b in items[:4]:
            lines.append(f"\n**{b.get('nama_beasiswa', '-')}**")
            lines.append(f"  • Penyelenggara: {b.get('penyelenggara', '-')}")
            lines.append(f"  • Sasaran: {b.get('sasaran', '-')}")
            if b.get("cakupan"):
                cakupan_text = b["cakupan"][:250]
                lines.append(f"  • Cakupan: {cakupan_text}")
            if b.get("persyaratan"):
                syarat_text = b["persyaratan"][:200]
                lines.append(f"  • Syarat: {syarat_text}")
            if b.get("cara_daftar"):
                lines.append(f"  • Cara daftar: {b['cara_daftar'][:150]}")
            if b.get("kontak"):
                lines.append(f"  • Kontak: {b['kontak']}")
            if b.get("catatan"):
                lines.append(f"  • ℹ️ {b['catatan']}")
            printed += 1

    qa_data = [d for d in data if "jawaban" in d and "nama_beasiswa" not in d]
    if qa_data:
        lines.append("")
        for qa in qa_data[:1]:
            lines.append(qa.get("jawaban", ""))

    lines.append("\nℹ️ Cek status beasiswa: **ssat.ukdw.ac.id** > Keuangan > Beasiswa")
    lines.append("📧 Info beasiswa: **beasiswa@ukdw.ac.id**")
    return "\n".join(lines)
