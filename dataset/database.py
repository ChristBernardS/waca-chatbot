"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     WACA — UKDW's Personal Chatbot System                    ║
║                              database.py  (v3)                               ║
║         PostgreSQL — Data Lengkap Real UKDW (Updated April 2026)             ║
╚══════════════════════════════════════════════════════════════════════════════╝

PEMBARUAN v3 (dari v2):
  • Tabel beasiswa  : Data lengkap dari ukdw.ac.id/beasiswa/
                      15 program beasiswa internal + eksternal + pinjaman
  • Tabel pertukaran_mahasiswa : Data lengkap dari ukdw.ac.id/en/oia/
                      Program exchange, short-term, summercamp 
  • Tabel jadwal_pendaftaran   : Jadwal terbaru 2026/2027
                      dari pmb.ukdw.ac.id/info/prestasi/ & /info/reguler/
                      (mencakup lulusan 2024, 2025, dan 2026)

SUMBER DATA:
  • ukdw.ac.id/beasiswa/          — Daftar lengkap beasiswa UKDW
  • ukdw.ac.id/en/oia/            — Program OIA: exchange & short-term
  • pmb.ukdw.ac.id/info/prestasi/ — Jadwal Seleksi Prestasi terbaru
  • pmb.ukdw.ac.id/info/reguler/  — Jadwal Seleksi Mandiri/SKL/UTBK terbaru
  • Dataset CSV Biro 1, 3, 4, PMB — Basis pengetahuan Q&A
"""

from pathlib import Path

import asyncpg
import logging
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL"
)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _buat_skema(conn)
        logger.info("Skema database diverifikasi / dibuat.")
        jumlah = await conn.fetchval("SELECT COUNT(*) FROM pengetahuan")
        if jumlah == 0:
            await _isi_data(conn)
            logger.info("Data UKDW v3 berhasil diisi ke database.")
        else:
            logger.info(f"Data sudah ada ({jumlah} entri). Lewati pengisian.")



async def log_unanswered_question(
    pertanyaan: str,
    intent: str,
    entitas: dict,
) -> None:
    """
    Simpan pertanyaan yang tidak dapat dijawab sistem ke tabel
    `pertanyaan_tidak_terjawab`.

    Logika de-duplikasi:
        Jika pertanyaan yang PERSIS SAMA sudah ada dengan status 'baru'
        atau 'ditinjau', cukup tambahkan `jumlah_ditanya` += 1 dan
        perbarui `waktu` agar muncul di urutan teratas.
        Ini mencegah baris duplikat untuk pertanyaan populer yang belum
        ditangani admin, sekaligus memberi sinyal prioritas ke admin.

    Args:
        pertanyaan : Teks asli pertanyaan pengguna.
        intent     : Intent yang terdeteksi Stage 1 (mis. "general").
        entitas    : Dict entitas hasil ekstraksi Stage 1.
    """
    import json as _json

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing_id = await conn.fetchval(
            """
            SELECT id FROM pertanyaan_tidak_terjawab
            WHERE pertanyaan = $1
              AND status IN ('baru', 'ditinjau')
            LIMIT 1
            """,
            pertanyaan,
        )

        if existing_id:
            await conn.execute(
                """
                UPDATE pertanyaan_tidak_terjawab
                   SET jumlah_ditanya = jumlah_ditanya + 1,
                       waktu          = NOW()
                 WHERE id = $1
                """,
                existing_id,
            )
            logger.info(
                f"[LOG_PTJ] Pertanyaan yang sama ditanya lagi (id={existing_id}): "
                f"'{pertanyaan[:60]}…'"
            )
        else:
            await conn.execute(
                """
                INSERT INTO pertanyaan_tidak_terjawab
                    (pertanyaan, intent, entitas)
                VALUES ($1, $2, $3::jsonb)
                """,
                pertanyaan,
                intent,
                _json.dumps(entitas, ensure_ascii=False),
            )
            logger.info(
                f"[LOG_PTJ] Pertanyaan tidak terjawab disimpan: "
                f"intent={intent} | '{pertanyaan[:60]}…'"
            )


async def get_unanswered_questions(
    status: str = "baru",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Ambil daftar pertanyaan tidak terjawab untuk ditampilkan di panel admin.

    Args:
        status : Filter status — 'baru' | 'ditinjau' | 'selesai' | 'semua'
        limit  : Jumlah maksimal baris yang dikembalikan (default 50).
        offset : Offset untuk paginasi (default 0).

    Returns:
        List of dict dengan kunci: id, pertanyaan, intent, entitas, waktu,
        status, catatan_admin, jumlah_ditanya.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status == "semua":
            rows = await conn.fetch(
                """
                SELECT id, pertanyaan, intent, entitas::text AS entitas,
                       waktu, status, catatan_admin, jumlah_ditanya
                  FROM pertanyaan_tidak_terjawab
                 ORDER BY
                    CASE status WHEN 'baru' THEN 0 WHEN 'ditinjau' THEN 1 ELSE 2 END,
                    jumlah_ditanya DESC,
                    waktu DESC
                 LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, pertanyaan, intent, entitas::text AS entitas,
                       waktu, status, catatan_admin, jumlah_ditanya
                  FROM pertanyaan_tidak_terjawab
                 WHERE status = $1
                 ORDER BY jumlah_ditanya DESC, waktu DESC
                 LIMIT $2 OFFSET $3
                """,
                status,
                limit,
                offset,
            )
    return [dict(r) for r in rows]


async def update_unanswered_status(
    id: int,
    status: str,
    catatan_admin: str = None,
) -> bool:
    """
    Perbarui status dan catatan admin untuk satu entri pertanyaan tidak terjawab.

    Args:
        id            : ID entri yang akan diperbarui.
        status        : Status baru — 'baru' | 'ditinjau' | 'selesai'.
        catatan_admin : Opsional — catatan dari admin (mis. "Sudah ditambahkan ke FAQ").

    Returns:
        True jika baris berhasil diperbarui, False jika ID tidak ditemukan.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pertanyaan_tidak_terjawab
               SET status        = $2,
                   catatan_admin = COALESCE($3, catatan_admin)
             WHERE id = $1
            """,
            id,
            status,
            catatan_admin,
        )
    updated = int(result.split()[-1]) > 0
    if updated:
        logger.info(f"[LOG_PTJ] Status id={id} diperbarui ke '{status}'.")
    else:
        logger.warning(f"[LOG_PTJ] ID={id} tidak ditemukan saat update status.")
    return updated


async def _buat_skema(conn):
    await conn.execute("""
        -- Tabel basis pengetahuan Q&A (dari 4 dataset CSV)
        CREATE TABLE IF NOT EXISTS pengetahuan (
            id          SERIAL PRIMARY KEY,
            unit_kerja  TEXT NOT NULL,     -- biro_1, biro_3, biro_4, pmb
            kategori    TEXT NOT NULL,
            pertanyaan  TEXT NOT NULL,
            jawaban     TEXT NOT NULL,
            kata_kunci  TEXT
        );

        -- Tabel program studi UKDW
        CREATE TABLE IF NOT EXISTS program_studi (
            id          SERIAL PRIMARY KEY,
            nama_prodi  TEXT NOT NULL UNIQUE,
            jenjang     TEXT NOT NULL,     -- S1, S2, S3, Profesi
            fakultas    TEXT,
            akreditasi  TEXT,
            deskripsi   TEXT
        );

        -- Tabel biaya kuliah per prodi 2025/2026
        -- DPFP  = Dana Pengembangan Fasilitas Pendidikan (sekali masuk)
        -- SPP Tetap = per semester, nominal tetap
        -- SPP Variabel = per SKS yang diambil
        CREATE TABLE IF NOT EXISTS biaya_kuliah (
            id                      SERIAL PRIMARY KEY,
            nama_prodi              TEXT NOT NULL,
            jenjang                 TEXT DEFAULT 'S1',
            dpfp                    BIGINT,
            ice_per_level           BIGINT,
            spp_tetap_per_semester  BIGINT,
            spp_variabel_per_sks    BIGINT,
            tahun_akademik          TEXT DEFAULT '2025/2026',
            catatan                 TEXT,
            CONSTRAINT fk_prodi FOREIGN KEY (nama_prodi) REFERENCES program_studi(nama_prodi) ON DELETE CASCADE
        );

        -- Tabel jalur pendaftaran mahasiswa baru (6 jalur resmi UKDW)
        CREATE TABLE IF NOT EXISTS jalur_pendaftaran (
            id              SERIAL PRIMARY KEY,
            nama_jalur      TEXT NOT NULL UNIQUE,
            deskripsi       TEXT,
            berlaku_untuk   TEXT,
            syarat_utama    TEXT,
            dokumen_wajib   TEXT,
            biaya_daftar    BIGINT,
            catatan_khusus  TEXT,
            website         TEXT DEFAULT 'https://pmb.ukdw.ac.id'
        );

        -- Tabel jadwal gelombang pendaftaran (terbaru 2026/2027)
        CREATE TABLE IF NOT EXISTS jadwal_pendaftaran (
            id                  SERIAL PRIMARY KEY,
            nama_jalur          TEXT NOT NULL,
            gelombang           TEXT,
            tanggal_buka        TEXT,
            tanggal_tutup       TEXT,
            tanggal_ujian       TEXT,
            tanggal_pengumuman  TEXT,
            keterangan          TEXT,
            CONSTRAINT fk_jalur FOREIGN KEY (nama_jalur) REFERENCES jalur_pendaftaran(nama_jalur) ON DELETE CASCADE
        );

        -- Tabel beasiswa (lengkap: internal UKDW + mitra + pemerintah)
        -- Kategori: 'mahasiswa_baru', 'mahasiswa_aktif', 'eksternal', 'pinjaman'
        CREATE TABLE IF NOT EXISTS beasiswa (
            id              SERIAL PRIMARY KEY,
            nama_beasiswa   TEXT NOT NULL,
            penyelenggara   TEXT,
            kategori        TEXT,           -- mahasiswa_baru / mahasiswa_aktif / eksternal / pinjaman
            jenis           TEXT,           -- full / sebagian / pinjaman
            sasaran         TEXT,           -- misal: "S1 kecuali Kedokteran"
            cakupan         TEXT,           -- rincian apa yang ditanggung/diberikan
            persyaratan     TEXT,
            cara_daftar     TEXT,
            kontak          TEXT,
            aktif           BOOLEAN DEFAULT TRUE,
            catatan         TEXT
        );

        -- Tabel program pertukaran mahasiswa (student exchange + short-term)
        -- Sumber: ukdw.ac.id/en/oia/
        CREATE TABLE IF NOT EXISTS pertukaran_mahasiswa (
            id                  SERIAL PRIMARY KEY,
            nama_program        TEXT NOT NULL,
            tanggal_mulai       TEXT,          -- Tanggal mulai program (YYYY-MM-DD atau teks deskriptif)
            tanggal_selesai     TEXT,          -- Tanggal selesai program
            tanggal_pendaftaran TEXT,          -- Deadline pendaftaran ke OIA UKDW
            universitas_mitra   TEXT,
            negara              TEXT,
            kategori            TEXT,          -- 'outbound' atau 'inbound'
            jenis_program       TEXT,          -- student_exchange / short_term / inbound_exchange / inbound_short_term
            durasi              TEXT,
            persyaratan         TEXT,
            pendanaan           TEXT,
            kontak              TEXT,
            deskripsi           TEXT
        );

        -- Tabel rate limiting — menyimpan jumlah pertanyaan per user per hari
        CREATE TABLE IF NOT EXISTS rate_limits (
            identifier  TEXT    NOT NULL,
            tanggal     DATE    NOT NULL,
            jumlah      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (identifier, tanggal)
        );

        CREATE INDEX IF NOT EXISTS idx_rate_limits_tanggal ON rate_limits (tanggal);

        -- ─────────────────────────────────────────────────────────────────────
        -- Tabel pertanyaan_tidak_terjawab
        -- Menyimpan pertanyaan pengguna yang tidak dapat dijawab sistem karena:
        --   • Tidak ada data yang cocok di database (raw_data kosong)
        --   • Intent terdeteksi sebagai "general" (topik di luar cakupan Waca)
        --
        -- Tujuan: Admin UKDW dapat mereview tabel ini untuk:
        --   1. Menambahkan entri baru ke tabel `pengetahuan` (knowledge base)
        --   2. Memperluas data prodi, beasiswa, jadwal, dll.
        --   3. Memperbaiki prompt orchestration agar intent lebih akurat
        --
        -- Kolom status: 'baru' → belum ditinjau, 'ditinjau' → sudah dilihat admin,
        --               'selesai' → knowledge base sudah diperbarui
        -- ─────────────────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pertanyaan_tidak_terjawab (
            id              SERIAL      PRIMARY KEY,
            pertanyaan      TEXT        NOT NULL,
            intent          TEXT        NOT NULL DEFAULT 'general',
            entitas         JSONB       NOT NULL DEFAULT '{}',
            waktu           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status          TEXT        NOT NULL DEFAULT 'baru'
                                        CHECK (status IN ('baru', 'ditinjau', 'selesai')),
            catatan_admin   TEXT,
            jumlah_ditanya  INTEGER     NOT NULL DEFAULT 1
        );

        -- Index untuk memudahkan admin memfilter berdasarkan status dan waktu
        CREATE INDEX IF NOT EXISTS idx_ptj_status ON pertanyaan_tidak_terjawab (status);
        CREATE INDEX IF NOT EXISTS idx_ptj_waktu  ON pertanyaan_tidak_terjawab (waktu DESC);
    """)


async def _isi_data(conn):
    await _isi_pengetahuan(conn)
    await _isi_program_studi(conn)
    await _isi_biaya_kuliah(conn)
    await _isi_jalur_pendaftaran(conn)
    await _isi_jadwal_pendaftaran(conn)
    await _isi_beasiswa(conn)
    await _isi_pertukaran_mahasiswa(conn)


async def _isi_pengetahuan(conn):
    data = [
        ("biro_1","administrasi nilai","Bagaimana cara melihat transkrip nilai semesteran?","Masuk ke ssat.ukdw.ac.id, login dengan akun mahasiswa, akses menu Data Akademik > Daftar Nilai, lalu unduh transkrip nilai. Jika perlu legalisasi, cetak dan serahkan ke Biro 1.","transkrip nilai ssat daftar nilai legalisasi"),
        ("biro_1","cuti akademik","Bagaimana cara mengajukan cuti akademik?","Masuk ke ssat.ukdw.ac.id, menu Layanan > Cuti Studi. Ajukan sebelum batas waktu (biasanya sebelum hari terakhir pembayaran registrasi/SPP Variabel).","cuti akademik cuti studi ssat layanan"),
        ("biro_1","data diri","Bagaimana jika data pada SSAT ada yang salah?","Hubungi Biro 1 melalui WA 081392521604 pada jam kerja.","data diri ssat salah koreksi biro 1 WA"),
        ("biro_1","data diri","Bagaimana jika data pada PDDIKTI ada yang salah?","Hubungi fakultas masing-masing mahasiswa.","data pddikti salah koreksi fakultas"),
        ("biro_1","ijazah","Bagaimana prosedur pengambilan ijazah?","Ijazah diberikan saat wisuda (syarat: lulus yudisium). Jika inabsensia, ijazah diambil setelah wisuda dengan menunjukkan KTM/identitas ke Biro 1.","ijazah wisuda yudisium pengambilan inabsensia"),
        ("biro_1","kartu tanda mahasiswa","Bagaimana cara mendapatkan KTM?","Langsung menghubungi Bank BNI UKDW.","KTM kartu tanda mahasiswa BNI baru"),
        ("biro_1","kartu tanda mahasiswa","Apa yang harus dilakukan jika KTM hilang?","1) Dapatkan surat polisi (KTM adalah kartu debit). 2) Bayar denda Rp 10.000 ke Biro 2. 3) Hubungi Bank BNI UKDW untuk KTM baru.","KTM hilang surat polisi denda BNI biro 2"),
        ("biro_1","pendaftaran mahasiswa baru","Setelah diterima menjadi mahasiswa UKDW, apa langkah selanjutnya?","Setelah pembayaran, hubungi Biro 1 untuk mendapatkan NIM.","diterima mahasiswa baru NIM pembayaran biro 1"),
        ("biro_1","peminjaman ruang","Bagaimana cara meminjam ruang kelas?","Izin ke Biro 1 agar jadwal dan ruang tidak tabrakan.","pinjam ruang kelas izin jadwal biro 1"),
        ("biro_1","presensi","Bagaimana jika mahasiswa lupa melakukan presensi?","Hubungi dosen maksimal 2 minggu setelah kejadian. Jika lebih, hubungi Biro 1 untuk form yang perlu ditandatangani dosen.","presensi lupa absen dosen form biro 1"),
        ("biro_1","registrasi matakuliah","Bagaimana cara melakukan registrasi matakuliah?","Masuk ke website registrasi.ukdw.ac.id. Lalu pilih program studi. Masuk menggunakan akun mahasiswa. Anda hanya bisa menambahkan, mengubah, atau membatalkan matakuliah selama jadwal registrasi matakuliah yang diberitakan oleh pihak kampus.","registrasi matakuliah registrasi.ukdw.ac.id matkul"),
        ("biro_1","registrasi matakuliah","Bagaimana cara mengubah atau membatalkan matakuliah?","Perubahan/pembatalan saat jadwal batal-tambah. Untuk pembatalan di transkrip, bisa dilakukan saat proses yudisium.","ubah batalkan matakuliah batal tambah KRS yudisium matkul"),
        ("biro_1","registrasi matakuliah","Apa yang harus dilakukan jika ada masalah dengan status registrasi matakuliah?","2 minggu sebelum registrasi akan muncul cekal/blokir SSAT. Cara membuka cekal ada di halaman home SSAT.","cekal blokir ssat registrasi matakuliah matkul"),
        ("biro_1","surat keterangan aktif","Bagaimana cara mengurus surat keterangan aktif kuliah?","Pergi ke website ssat.ukdw.ac.id. Lalu akses menu layanan dan pilih opsi surat aktif kuliah. Setelah itu anda akan diarahkan ke google form untuk mengisi data. Setelah selesai mengisi form, surat keterangan aktif akan dikirimkan via email dalam beberapa hari.","surat keterangan aktif kuliah ssat email"),
        ("biro_1","surat keterangan lulus","Bagaimana cara mengurus surat keterangan lulus?","Hubungi fakultas masing-masing.","surat keterangan lulus fakultas"),
        ("biro_1","wisuda","Bagaimana cara mengikuti wisuda?","Harus lulus yudisium terlebih dahulu, lalu ikuti prosedur di wisuda.ukdw.ac.id.","wisuda yudisium prosedur wisuda.ukdw.ac.id"),
        ("biro_1","yudisium","Bagaimana cara mendaftar yudisium?","Melalui website https://yudisium.ukdw.ac.id/index.php.","yudisium daftar website lulus"),
        ("biro_3","alumni","Apa keuntungan Kartu Alumni UKDW?","Diskon 10% layanan KAI (Kereta Api Indonesia) ke kota manapun, berlaku hingga Juli 2028. Dokumen seperti skripsi hanya dapat diakses saat menjadi mahasiswa aktif.","kartu alumni KAI diskon kereta api"),
        ("biro_3","asuransi","Apa cakupan klaim asuransi kecelakaan mahasiswa UKDW?","Mencakup rawat jalan dan rawat inap, selama tidak melebihi batas biaya yang disediakan kampus.","asuransi kecelakaan klaim rawat jalan rawat inap"),
        ("biro_3","asuransi","Bagaimana prosedur pengajuan asuransi kecelakaan?","Ajukan klaim ke Biro 3 (dengan data lengkap: nama, NIM, dll.). Kampus memberikan dana reimburse setelah klaim diverifikasi.","asuransi prosedur klaim reimburse biro 3"),
        ("biro_3","beasiswa","Apa saja jenis beasiswa di UKDW?","UKDW memiliki banyak beasiswa: untuk mahasiswa baru (UKDW Scholarship, Talenta, Samapta, KIP-Kuliah, ADiK) dan mahasiswa aktif (Kebutuhan, BPD DIY, Prestasi Akademik, Prestasi Umum, ADARO, Scranton, dan lainnya). Status beasiswa bisa dicek di ssat.ukdw.ac.id > Keuangan > Beasiswa.","beasiswa jenis UKDW Scholarship Talenta KIP Scranton ADARO"),
        ("biro_3","beasiswa","Bagaimana cara mengecek status beasiswa?","Buka ssat.ukdw.ac.id, klik menu Keuangan > Beasiswa. Semua riwayat beasiswa ada di sana.","status beasiswa cek ssat keuangan riwayat"),
        ("biro_3","jas almamater dan toga","Apakah jas almamater/toga bisa ditukar jika ada masalah?","Tidak bisa ditukar setelah dibawa pulang. Harus dicek dan dilaporkan saat pengambilan.","jas almamater toga tukar ukuran rusak pengambilan"),
        ("biro_3","lomba dan kompetisi","Apakah kampus memberi dana bantuan untuk lomba offline luar kota?","Jika ditugaskan kampus, mendapat akomodasi: biaya pendaftaran, kendaraan, dll.","lomba kompetisi dana bantuan akomodasi luar kota"),
        ("biro_3","organisasi mahasiswa","Apakah perjanjian sponsorship BEM harus diketahui Biro 3?","Iya, minimal diketahui Wakil Dekan 3 dan Biro Kemahasiswaan.","BEM sponsor perjanjian biro 3 wakil dekan"),
        ("biro_3","organisasi mahasiswa","Bagaimana cara organisasi luar UKDW mengajukan kerjasama untuk event di kampus?","Ajukan ke fakultas dan Biro Kemahasiswaan. Jika pakai gedung Koinonia/Agape, ajukan juga ke unit Kerumah-tanggaan.","organisasi luar event gedung koinonia agape"),
        ("biro_3","organisasi mahasiswa","Bagaimana cara mengajukan proposal kegiatan organisasi?","LPJ dan proposal ditandatangani ketua, sekretaris, Biro 3, WD 3, dan WR 3.","proposal kegiatan LPJ tanda tangan wakil dekan rektor"),
        ("biro_3","organisasi mahasiswa","Apakah Biro 3 memiliki database perusahaan mitra untuk sponsor?","Ya, antara lain Djarum, BCA, Supindo.","sponsor mitra perusahaan database BEM Djarum BCA"),
        ("biro_3","karir dan perusahaan","Apakah Biro 3 mengurus izin walk-in interview di kampus?","Iya, Biro 3 yang mengurus izin lokasi dan publikasi.","walk-in interview izin lokasi biro 3"),
        ("biro_3","karir dan perusahaan","Bagaimana prosedur jika perusahaan ingin walk-in interview di UKDW?","Serahkan surat permohonan, judul kegiatan, narasumber, tanggal, tempat, dan target audien ke Biro 3.","walk-in interview prosedur surat permohonan biro 3"),
        ("biro_3","karir dan perusahaan","Bagaimana prosedur job fair tingkat fakultas?","Biro 3 membuat panitia, publikasi, dan menghubungi perusahaan dengan detail booth, tarif, konsumsi, dll.","job fair panitia publikasi booth biro 3"),
        ("biro_3","karir dan perusahaan","Apakah UKDW punya portal job board untuk mahasiswa/alumni?","Ya, Instagram @pusatkarir_ukdw membahas lowongan yang bekerja sama langsung dengan UKDW.","job board lowongan pekerjaan instagram pusatkarir_ukdw"),
        ("biro_3","pinjaman registrasi","Bagaimana cara mengajukan pinjaman registrasi?","Isi formulir di Biro 3. Pinjaman berupa cicilan maksimal 75% biaya SKS Variabel per semester. Tidak ada bunga.","pinjaman registrasi formulir biro 3 tanpa bunga"),
        ("biro_3","PKM","Bagaimana Biro 3 memfasilitasi bimbingan proposal PKM?","Biro 3 koordinasikan tim pendamping untuk review internal (UKDW) dan eksternal sebelum unggah ke Simbelmawa.","PKM proposal bimbingan simbelmawa review biro 3"),
        ("biro_3","program wajib kampus","Apa saja program wajib sebagai syarat yudisium?","OKA (Orientasi Kehidupan Akademika), P3DM (Pengembangan Potensi Diri Mahasiswa), PKLM (Pelatihan Kepemimpinan dan Manajemen), dan program wajib fakultas.","program wajib OKA P3DM PKLM syarat yudisium"),
        ("biro_3","surat izin presensi","Bagaimana mekanisme surat izin presensi untuk kegiatan organisasi?","Hubungi Biro 3 untuk surat tugas izin kuliah, yang bisa digunakan di eclass.ukdw.ac.id.","surat izin presensi organisasi eclass biro 3"),
        ("biro_3","tata tertib","Batas kewenangan Biro 3 dalam pelanggaran tata tertib non-akademik?","Biro 3 dapat memberikan SK Rektor tentang jenis pelanggaran dan pengurangan poin SAC.","tata tertib pelanggaran SAC biro 3"),
        ("biro_3","tracer study","Kapan pengisian Tracer Study dilakukan?","Tahun berikutnya dari wisuda (contoh: wisuda 2024 → Tracer Study Januari-November 2025). Berbentuk survei online tentang karir dan kepuasan alumni.","tracer study alumni survei wisuda tahunan"),
        ("biro_4","kerjasama perusahaan","Bagaimana prosedur perusahaan yang ingin kerjasama dengan UKDW?","Buat surat ke Fakultas terkait. Biro 4 akan fasilitasi koordinasi selanjutnya.","kerjasama perusahaan fakultas surat prosedur biro 4"),
        ("biro_4","kerjasama perusahaan","Apakah ada template MoU/MoA?","Ya. Wajib ada meeting/diskusi dulu dengan unit terkait sebelum dokumen dibuat. Biro 4 akan menyusun dokumen setelah komunikasi awal.","MoU MoA template dokumen kerjasama biro 4"),
        ("biro_4","kerjasama perusahaan","Berapa lama proses MoU hingga penandatanganan Rektor?","Satu hingga dua minggu.","MoU PKS IA waktu proses penandatanganan rektor"),
        ("biro_4","kerjasama perusahaan","Apakah ada alur resmi jika prodi/fakultas dihubungi langsung oleh perusahaan?","Ada alur: pengajuan di sistem informasi kerjasama UKDW hingga review dokumen.","alur kerjasama sistem informasi review"),
        ("biro_4","kerjasama perusahaan","Apakah Biro 4 memiliki daftar mitra dengan MoU aktif?","Ya. Daftar dapat dilihat di laman Kerja Sama website resmi UKDW (www.ukdw.ac.id).","daftar mitra MoU aktif website kerjasama"),
        ("biro_4","kerjasama perusahaan","Bagaimana alur mendaftarkan perusahaan mitra baru sebagai tempat magang?","Buat surat permohonan resmi ke Rektor UKDW.","magang tempat magang surat permohonan rektor mitra baru"),
        ("biro_4","kerjasama perusahaan","Bagaimana cara instansi/perusahaan menjalin kerjasama dengan UKDW?","Hubungi Biro 4: Email kerjasama@staff.ukdw.ac.id | Telp (0274) 563929 ext. 118. Kirim surat permohonan kerjasama.","kerjasama instansi email kerjasama@staff.ukdw.ac.id telepon biro 4"),
        ("biro_4","kerjasama perusahaan","Apa bentuk kerjasama yang bisa dilakukan dengan UKDW?","Penelitian & pengabdian masyarakat, magang/rekrutmen, pelatihan/seminar/workshop, pengembangan kurikulum, pertukaran dosen/mahasiswa.","bentuk kerjasama penelitian magang seminar workshop"),
        ("biro_4","kerjasama perusahaan","Apakah UKDW menerima kerjasama magang/rekrutmen?","Ya. Career Center UKDW mengelola kerjasama rekrutmen dan magang. Kontak: ppkpk@staff.ukdw.ac.id.","magang rekrutmen career center ppkpk@staff.ukdw.ac.id"),
        ("biro_4","student exchange","Apakah UKDW punya program student exchange?","Ya. Info di www.ukdw.ac.id/en/oia. Koordinasi melalui Biro 4 / OIA UKDW.","student exchange pertukaran OIA"),
        ("biro_4","student exchange","Apa saja universitas mitra luar negeri untuk student exchange?","Korea: Handong Global University, KMOU (Korea Maritime and Ocean University). Taiwan: I-Shou University, Tunghai University. USA: Goshen College, Ouachita Baptist University, The Ohio University. Filipina: Philippine Normal University. Daftar lengkap di www.ukdw.ac.id/en/oia.","universitas mitra Korea Taiwan USA Filipina OIA exchange"),
        ("biro_4","student exchange","Bagaimana alur pendaftaran student exchange?","Informasikan ke Biro 4/OIA dulu, lalu ikuti proses seleksi internal, nominasi ke universitas mitra, dan seleksi final oleh universitas mitra.","pendaftaran student exchange alur biro 4 OIA"),
        ("biro_4","student exchange","Dokumen apa yang dibutuhkan untuk student exchange?","KTM, transkrip nilai (dalam Bahasa Inggris), surat rekomendasi dari fakultas, motivation letter/study plan, foto, copy paspor (direkomendasikan), financial statement, dan dokumen tambahan sesuai persyaratan universitas mitra.","dokumen student exchange KTM transkrip rekomendasi paspor"),
        ("biro_4","student exchange","Apakah SPP/SKS tetap dibayar ke UKDW saat exchange?","Ya, tetap dibayar ke UKDW.","SPP SKS biaya student exchange tetap UKDW"),
        ("biro_4","student exchange","Bagaimana prosedur konversi SKS dari program exchange?","Konsultasi dengan fakultas/prodi untuk penentuan konversi/transfer SKS.","konversi transfer SKS matakuliah prodi matkul"),
        ("biro_4","student exchange","Apakah Biro 4 membantu visa pelajar?","Biro 4 bantu visa mahasiswa asing di UKDW. Mahasiswa UKDW ke luar negeri bisa konsultasi ke Biro 4, tapi proses visa tanggung jawab mahasiswa.","visa pelajar luar negeri biro 4 konsultasi"),
        ("biro_4","iklan baliho videotron","Bagaimana prosedur pemasangan iklan di Baliho/Videotron UKDW?","Buat surat permohonan ke Kepala Biro Kerjasama dan Relasi Publik (Biro 4).","iklan baliho videotron surat permohonan biro 4"),
        ("biro_4","iklan baliho videotron","Kontak Biro 4 untuk kerjasama dan iklan?","Email: kerjasama@staff.ukdw.ac.id | Telepon: (0274) 563929 ext. 118.","kontak kerjasama email telepon biro 4 ext 118"),
        ("biro_4","iklan baliho videotron","Apakah UKDW memiliki template resmi MoU/PKS/IA?","Ya. Disusun setelah komunikasi awal dengan Biro 4.","template MoU PKS IA dokumen standar biro 4"),
        ("biro_4","iklan baliho videotron","Bagaimana melihat daftar mitra yang sudah bekerjasama dengan UKDW?","Lihat laman Kerja Sama di www.ukdw.ac.id — mencakup sektor pendidikan, pemerintah, industri, dan lembaga sosial.","daftar mitra website kerjasama sektor"),
        ("biro_4","iklan baliho videotron","Berapa lama proses MoU/PKS/IA biasanya berlangsung?","Sekitar 1-2 minggu setelah draft disetujui, tergantung kompleksitas kerjasama.","proses waktu MoU PKS IA penandatanganan"),
        ("pmb","pendaftaran","Kapan jadwal penerimaan mahasiswa baru UKDW dibuka?","PMB UKDW dibuka mulai September dan berlangsung hingga Agustus tahun berikutnya dengan beberapa gelombang. Info lengkap di pmb.ukdw.ac.id.","jadwal pendaftaran PMB September Agustus gelombang"),
        ("pmb","pendaftaran","Apa saja jalur pendaftaran yang tersedia di UKDW?","Ada 6 jalur: (1) Seleksi Prestasi — nilai rapor 10-11, tanpa tes; (2) Seleksi Mandiri — nilai rapor, semua angkatan; (3) Seleksi SKL — pakai SKL; (4) Seleksi UTBK — nilai UTBK ≥350; (5) Seleksi Kedokteran — khusus kedokteran, ada tes 2 hari; (6) Seleksi Filsafat Keilahian — khusus teologi. Info di pmb.ukdw.ac.id.","jalur pendaftaran prestasi mandiri SKL UTBK kedokteran filsafat teologi"),
        ("pmb","biaya pendaftaran","Berapa biaya pendaftaran dan biaya kuliah mahasiswa baru UKDW?","Biaya daftar semua jalur Rp 200.000. Biaya kuliah per prodi berbeda. Contoh: Informatika DPFP Rp 22 juta + SPP Tetap Rp 3,75 juta/sem. Kedokteran DPFP Rp 290 juta. Detail di pmb.ukdw.ac.id.","biaya pendaftaran kuliah DPFP SPP variabel tetap informatika kedokteran"),
        ("pmb","syarat pendaftaran","Apa saja persyaratan pendaftaran UKDW?","Syarat umum: lulusan SMA/SMK (bukan homeschooling), nilai rapor min 75 (Prestasi). Dokumen: formulir, rapor 10-11 dilegalisir, foto 3x4. Arsitektur/Desain Produk/Biologi: surat bebas buta warna. Arsitektur: sketsa + video. Kedokteran: rapor 10-12, ijazah, KTP ortu, formulir kesehatan.","syarat pendaftaran dokumen rapor bebas buta warna arsitektur kedokteran"),
        ("pmb","pendaftaran","Bagaimana cara mengetahui hasil seleksi PMB UKDW?","Pihak PMB menghubungi calon mahasiswa melalui WhatsApp setelah pengumuman.","hasil seleksi pengumuman WhatsApp PMB"),
        ("pmb","beasiswa pendaftaran","Apakah ada beasiswa untuk calon mahasiswa baru UKDW?","Ada: UKDW Scholarship (full, prestasi akademik/non-akademik), Talenta (full, tidak mampu), Samapta (TNI-AD), KIP-Kuliah (tidak mampu), ADiK (Papua/3T). Info lengkap di pmb.ukdw.ac.id.","beasiswa mahasiswa baru UKDW Scholarship Talenta KIP ADiK Samapta"),
        ("pmb","pendaftaran","Bagaimana cara melakukan pendaftaran ulang setelah diterima di UKDW?","Lakukan pembayaran Biaya 1 yang diinformasikan PMB saat pengumuman penerimaan.","pendaftaran ulang diterima pembayaran biaya 1"),
        ("pmb","biaya pendaftaran","Berapa harga asrama teologi UKDW?","Rp 1.350.000, termasuk makan siang, malam, dan bus.","asrama teologi harga biaya makan bus"),
        ("pmb","biaya pendaftaran","Berapa biaya koas kedokteran UKDW?","Biaya koas kedokteran UKDW adalah Rp 34.000.000.","koas kedokteran biaya profesi"),
        ("pmb","beasiswa pendaftaran","UKDW bekerjasama dengan siapa untuk beasiswa?","Djarum Foundation, GKI (Gereja Kristen Indonesia), GKJ (Gereja Kristen Jawa), Bank BPD DIY, Adaro Foundation, Scranton University (Korea), BNI, GKJW, GKP, dan lainnya.","kerjasama beasiswa Djarum GKI GKJ BPD DIY ADARO Scranton mitra"),
    ]
    await conn.executemany(
        "INSERT INTO pengetahuan (unit_kerja, kategori, pertanyaan, jawaban, kata_kunci) VALUES ($1,$2,$3,$4,$5)",
        data
    )


async def _isi_program_studi(conn):
    await conn.executemany(
        "INSERT INTO program_studi (nama_prodi, jenjang, fakultas, akreditasi, deskripsi) VALUES ($1,$2,$3,$4,$5)",
        [
            ("Filsafat Keilahian","S1","Fakultas Teologi","Unggul","Kajian teologi, filsafat, dan kehidupan kristiani."),
            ("Arsitektur","S1","Fakultas Arsitektur dan Desain","Unggul","Perancangan bangunan dan lingkungan binaan yang fungsional dan estetis."),
            ("Desain Produk","S1","Fakultas Arsitektur dan Desain","Unggul","Perancangan produk industri inovatif berorientasi pengguna."),
            ("Manajemen","S1","Fakultas Bisnis","Unggul","Strategi bisnis, pemasaran, keuangan, dan manajemen SDM."),
            ("Akuntansi","S1","Fakultas Bisnis","Unggul","Akuntansi keuangan, perpajakan, auditing, dan sistem informasi akuntansi."),
            ("Biologi","S1","Fakultas Bioteknologi","B","Genetika, ekologi, biokimia, dan bioteknologi terapan."),
            ("Informatika","S1","Fakultas Teknologi Informasi","Unggul","Rekayasa perangkat lunak, kecerdasan buatan, dan pengembangan sistem."),
            ("Sistem Informasi","S1","Fakultas Teknologi Informasi","Unggul","Pengelolaan sistem informasi bisnis dan teknologi enterprise."),
            ("Pendidikan Bahasa Inggris","S1","Fakultas Keguruan dan Ilmu Pendidikan","B","Pendidikan dan pengajaran Bahasa Inggris di jenjang SMP dan SMA."),
            ("Studi Humanitas","S1","Fakultas Bisnis","B","Kajian lintas-disiplin humaniora, sosial, dan budaya."),
            ("Kedokteran","S1","Fakultas Kedokteran","B","Pendidikan pre-klinik dan klinik ilmu kedokteran."),
            ("Magister Manajemen","S2","Fakultas Bisnis","B","MBA: manajemen strategis dan kewirausahaan."),
            ("Magister Arsitektur","S2","Fakultas Arsitektur dan Desain","B","Riset lanjutan dan desain arsitektur kontemporer."),
        ]
    )


async def _isi_biaya_kuliah(conn):
    await conn.executemany(
        "INSERT INTO biaya_kuliah (nama_prodi,jenjang,dpfp,ice_per_level,spp_tetap_per_semester,spp_variabel_per_sks,tahun_akademik,catatan) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        [
            ("Filsafat Keilahian","S1",13000000,750000,3000000,250000,"2025/2026","Asrama teologi: Rp 1.350.000/bulan (termasuk makan siang, malam, bus)."),
            ("Arsitektur","S1",22000000,750000,3500000,250000,"2025/2026",""),
            ("Desain Produk","S1",20000000,750000,3500000,250000,"2025/2026",""),
            ("Manajemen","S1",20000000,750000,3500000,250000,"2025/2026",""),
            ("Akuntansi","S1",20000000,750000,3500000,250000,"2025/2026",""),
            ("Biologi","S1",20000000,750000,3500000,250000,"2025/2026",""),
            ("Informatika","S1",22000000,750000,3750000,250000,"2025/2026",""),
            ("Sistem Informasi","S1",22000000,750000,3750000,250000,"2025/2026",""),
            ("Pendidikan Bahasa Inggris","S1",17500000,750000,3000000,250000,"2025/2026",""),
            ("Studi Humanitas","S1",20000000,750000,3000000,250000,"2025/2026",""),
            ("Kedokteran","S1",290000000,750000,15500000,950000,"2025/2026","Biaya koas: Rp 34.000.000. "),
        ]
    )


async def _isi_jalur_pendaftaran(conn):
    await conn.executemany(
        "INSERT INTO jalur_pendaftaran (nama_jalur,deskripsi,berlaku_untuk,syarat_utama,dokumen_wajib,biaya_daftar,catatan_khusus,website) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        [
            ("Seleksi Prestasi",
             "Seleksi berdasarkan nilai rapor kelas 10-11 (Bahasa Inggris dan Matematika). Tanpa tes tertulis. Tersedia tambahan beasiswa berdasarkan nilai rata-rata dan ranking.",
             "Semua prodi Non Kedokteran dan Non Filsafat Keilahian",
             "Lulusan SMA/SMK 2024, 2025, atau 2026 (bukan homeschooling). Nilai rata-rata rapor 10-11 min 75.",
             "Formulir pendaftaran, rapor 10-12 dilegalisir, foto 3x4. Arsitektur/Desain Produk/Biologi: surat bebas buta warna. Arsitektur: sketsa perspektif 1 lembar.",
             200000,
             "Berhak ikut 1 kali. Beasiswa tambahan TIDAK berlaku untuk Seleksi Prestasi yang menambahkan Beasiswa Gereja.",
             "https://pmb.ukdw.ac.id/info/prestasi/"),
            ("Seleksi Mandiri",
             "Seleksi berdasarkan nilai rapor 10-11, di luar skema Prestasi. Terbuka semua angkatan. Tanpa tes.",
             "Semua prodi Non Kedokteran dan Non Filsafat Keilahian",
             "Semua angkatan lulusan SMA/SMK (bukan homeschooling).",
             "Formulir pendaftaran, rapor 10-11 dilegalisir, foto 3x4. Arsitektur/Desain Produk/Biologi: surat bebas buta warna. Arsitektur: sketsa perspektif + video.",
             200000,
             "Berhak ikut 1 kali. Beasiswa tambahan berlaku untuk jalur ini.",
             "https://pmb.ukdw.ac.id/info/reguler/"),
            ("Seleksi SKL",
             "Seleksi menggunakan Surat Keterangan Lulus bagi yang belum punya ijazah resmi.",
             "Semua prodi Non Kedokteran dan Non Filsafat Keilahian",
             "Semua angkatan lulusan SMA/SMK (bukan homeschooling). Memiliki SKL.",
             "Formulir pendaftaran, scan SKL, foto 3x4. Arsitektur/Desain Produk/Biologi: surat bebas buta warna. Arsitektur: sketsa + video.",
             200000,
             "Beasiswa tambahan berlaku untuk jalur ini.",
             "https://pmb.ukdw.ac.id/info/reguler/"),
            ("Seleksi UTBK",
             "Seleksi menggunakan nilai UTBK dari SNBT nasional. Nilai UTBK minimal 350.",
             "Semua prodi Non Kedokteran dan Non Filsafat Keilahian",
             "Semua angkatan lulusan SMA/SMK (bukan homeschooling). Nilai UTBK ≥ 350.",
             "Formulir pendaftaran, scan sertifikat UTBK, foto 3x4. Arsitektur/Desain Produk/Biologi: surat bebas buta warna.",
             200000,
             "Beasiswa tambahan berlaku untuk jalur ini.",
             "https://pmb.ukdw.ac.id/info/reguler/"),
            ("Seleksi Kedokteran",
             "Tes 2 hari khusus prodi Kedokteran. Hari 1: Bahasa Inggris + Biologi & Pengetahuan Kesehatan (CBT). Hari 2: Tes Kesehatan & MMPI + Wawancara dengan Prodi.",
             "Khusus prodi Kedokteran",
             "Kelas 12 SMA IPA atau lulusan SMA IPA (2021-2026).",
             "Formulir pendaftaran, formulir kesehatan (unduh di pmb.ukdw.ac.id), rapor 10-12 dilegalisir, ijazah/SKHUN, KTP ortu, foto 3x4.",
             200000,
             "Pendaftaran ditutup 2 hari sebelum tes. Ada 4 gelombang per tahun.",
             "https://pmb.ukdw.ac.id"),
            ("Seleksi Filsafat Keilahian",
             "Tes 2 hari khusus prodi Filsafat Keilahian/Teologi. Hari 1: Tes Kompetensi Minat Studi + Tes Bahasa Inggris (online). Hari 2: Wawancara dengan Prodi.",
             "Khusus prodi Filsafat Keilahian",
             "Lulusan SMA/SMK. Pilihan pertama wajib Prodi Filsafat Keilahian.",
             "Formulir pendaftaran, rapor 10-12 dilegalisir, ijazah/SKHUN, KTP ortu, foto 3x4, surat rekomendasi sinode/gereja, Surat Baptis/Sidi, Surat Pernyataan bermeterai Rp 10.000.",
             200000,
             "Pendaftaran ditutup 1 hari sebelum tes. Ada 3 gelombang per tahun.",
             "https://pmb.ukdw.ac.id"),
        ]
    )


async def _isi_jadwal_pendaftaran(conn):
    """
    Jadwal PMB terbaru UKDW (diperbarui April 2026).
    Sumber: pmb.ukdw.ac.id/info/prestasi/ dan pmb.ukdw.ac.id/info/reguler/

    Catatan:
    - Seleksi Prestasi sekarang mencakup lulusan 2024, 2025, DAN 2026
    - Pola gelombang tahunan konsisten: September → Agustus
    - Untuk jadwal terbaru yang paling akurat, kunjungi pmb.ukdw.ac.id
    """
    await conn.executemany(
        "INSERT INTO jadwal_pendaftaran (nama_jalur,gelombang,tanggal_buka,tanggal_tutup,tanggal_ujian,tanggal_pengumuman,keterangan) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        [
            ("Seleksi Prestasi","Gelombang 1",
             "Pendaftaran dibuka hingga 1 Mei 2026","1 Mei 2026","Tanpa Tes","5 Mei 2026",
             "Gelombang awal tahun akademik 2026/2027. Tanpa tes — seleksi berdasarkan nilai rapor."),
            ("Seleksi Prestasi","Gelombang 2",
             "Pendaftaran dibuka hingga 8 Mei 2026","8 Mei 2026","Tanpa Tes","12 Mei 2026",
             "Gelombang awal tahun akademik 2026/2027. Tanpa tes — seleksi berdasarkan nilai rapor."),
            ("Seleksi Prestasi","Gelombang 3",
             "Pendaftaran dibuka hingga 15 Mei 2026","15 Mei 2026","Tanpa Tes","19 Mei 2026",
             "Tanpa tes — seleksi berdasarkan nilai rapor."),
            ("Seleksi Prestasi","Gelombang 4",
             "Pendaftaran dibuka hingga 22 Mei 2026","22 Mei 2026","Tanpa Tes","26 Mei 2026",
             "Tanpa tes — seleksi berdasarkan nilai rapor."),
            ("Seleksi Prestasi","Gelombang 5",
             "Pendaftaran dibuka hingga 29 Mei 2026","5 Juni 2026","Tanpa Tes","2 Juni 2026",
             "Gelombang akhir untuk Seleksi Prestasi. Tanpa tes — seleksi berdasarkan nilai rapor."),

            ("Seleksi Mandiri","Gelombang 1",
             "Pendaftaran dibuka hingga 7 Mei 2026","7 Mei 2026","Tanpa Tes",
             "12 Mei 2026",
             "Tersedia beberapa tanggal pilihan. Pengumuman 1 minggu setelah mendaftar."),
            ("Seleksi Mandiri","Gelombang 2",
             "Pendaftaran dibuka hingga 8 Mei 2026","8 Mei 2026","Tanpa Tes",
             "12 Mei 2026",
             "Pengumuman 1 minggu setelah mendaftar."),
            ("Seleksi Mandiri","Gelombang 3",
             "Pendaftaran dibuka hingga 15 Mei 2026","15 Mei 2026","Tanpa Tes",
             "19 Mei 2026",
             "Pengumuman 1 minggu setelah mendaftar."),
            ("Seleksi Mandiri","Gelombang 4",
             "Pendaftaran dibuka hingga 22 Mei 2026","22 Mei 2026","Tanpa Tes",
             "26 Mei 2026",
             "Pengumuman 1 minggu setelah mendaftar."),
            ("Seleksi Mandiri","Gelombang 5",
             "Pendaftaran dibuka hingga 29 Mei 2026","29 Mei 2026","Tanpa Tes",
             "2 Juni 2026",
             "Gelombang akhir untuk Seleksi Mandiri."),

            ("Seleksi SKL","Gelombang 1",
             "Juni 2025","Juni 2025","-",
             "Diinformasikan PMB via WhatsApp",
             "Pendaftaran mingguan. Untuk siswa yang baru lulus dan belum punya ijazah resmi."),
            ("Seleksi SKL","Gelombang 2",
             "Juli 2025","Juli 2025","-",
             "Diinformasikan PMB via WhatsApp",
             "Pendaftaran mingguan."),
            ("Seleksi SKL","Gelombang 3",
             "Agustus 2025","Agustus 2025","-",
             "Diinformasikan PMB via WhatsApp",
             "Gelombang akhir — batas akhir penerimaan sebelum awal kuliah."),

            ("Seleksi UTBK","Gelombang 1",
             "Juni 2026","Juni 2026","-",
             "Diinformasikan PMB via WhatsApp",
             "Nilai UTBK minimal 350. Pendaftaran mingguan."),
            ("Seleksi UTBK","Gelombang 2",
             "Juli 2026","Juli 2026","-",
             "Diinformasikan PMB via WhatsApp",
             "Pendaftaran mingguan."),
            ("Seleksi UTBK","Gelombang 3",
             "Agustus 2026","Agustus 2026","-",
             "Diinformasikan PMB via WhatsApp",
             "Gelombang akhir UTBK."),

            ("Seleksi Kedokteran","Gelombang 1",
             "Pendaftaran hingga 9 December 2025", None,
             "11-13 Desember 2025","15 Desember 2025",
             "Info jadwal pasti: hubungi PMB UKDW. Hari 1: Tes Kesehatan (di RS Bethesda YK) dengan biaya mandiri bagi yang memilih ONSITE. Tes Kesehatan dilakukan di RS terdekat daerah asal dengan biaya mandiri bagi yang memilih HYBRID, template form kesehatan dan hasil akan dikirimkan oleh petugas. Hari 2: Placement Test ICE, Tes Biologi dan Kesehatan Umum, TPA (HYBRID). Hari 3: Wawancara (HYBRID)"),
            ("Seleksi Kedokteran","Gelombang 2",
             "Pendaftaran hingga 19 Januari 2026",None,
             "22-24 Januari 2026","26 Januari 2026",
             "Info jadwal pasti: hubungi PMB UKDW. Hari 1: Tes Kesehatan (di RS Bethesda YK) dengan biaya mandiri bagi yang memilih ONSITE. Tes Kesehatan dilakukan di RS terdekat daerah asal dengan biaya mandiri bagi yang memilih HYBRID, template form kesehatan dan hasil akan dikirimkan oleh petugas. Hari 2: Placement Test ICE, Tes Biologi dan Kesehatan Umum, TPA (HYBRID). Hari 3: Wawancara (HYBRID)"),
            ("Seleksi Kedokteran","Gelombang 3",
             "Pendaftaran hingga 6 April 2026",None,
             "9-11 April 2026","13 April 2026",
             "Info jadwal pasti: hubungi PMB UKDW. Hari 1: Tes Kesehatan (di RS Bethesda YK) dengan biaya mandiri bagi yang memilih ONSITE. Tes Kesehatan dilakukan di RS terdekat daerah asal dengan biaya mandiri bagi yang memilih HYBRID, template form kesehatan dan hasil akan dikirimkan oleh petugas. Hari 2: Placement Test ICE, Tes Biologi dan Kesehatan Umum, TPA (HYBRID). Hari 3: Wawancara (HYBRID)"),
            ("Seleksi Kedokteran","Gelombang 4",
             "Pendaftaran hingga 8 Juni 2026",None,
             "11-13 Juni 2026","15 Juni 2026",
             "Info jadwal pasti: hubungi PMB UKDW. Hari 1: Tes Kesehatan (di RS Bethesda YK) dengan biaya mandiri bagi yang memilih ONSITE. Tes Kesehatan dilakukan di RS terdekat daerah asal dengan biaya mandiri bagi yang memilih HYBRID, template form kesehatan dan hasil akan dikirimkan oleh petugas. Hari 2: Placement Test ICE, Tes Biologi dan Kesehatan Umum, TPA (HYBRID). Hari 3: Wawancara (HYBRID)"),
            ("Seleksi Kedokteran","Gelombang UTBK",
             "Pendaftaran hingga 29 Juni 2026",None,
             "2-4 Juli 2026","6 Juli 2026",
             "Gelombang akhir Seleksi Kedokteran. Info pasti di pmb.ukdw.ac.id. Info jadwal pasti: hubungi PMB UKDW. Hari 1: Tes Kesehatan (di RS Bethesda YK) dengan biaya mandiri bagi yang memilih ONSITE. Tes Kesehatan dilakukan di RS terdekat daerah asal dengan biaya mandiri bagi yang memilih HYBRID, template form kesehatan dan hasil akan dikirimkan oleh petugas. Hari 2: Placement Test ICE, Tes Biologi dan Kesehatan Umum, TPA (HYBRID). Hari 3: Wawancara (HYBRID)"),


            ("Seleksi Filsafat Keilahian","Gelombang 1",
             "Pendaftaran ditutup 19 Mei 2026",None,
             "22-23 Mei 2026","29 Mei 2026",
             "Info jadwal pasti: hubungi PMB UKDW. Hari 1: Tes Bahasa Inggris dan Wawancara. Hari 2: Tes Kognitif dan Tes Kepribadian."),
            ("Seleksi Filsafat Keilahian","Gelombang 2",
             "Pendaftaran ditutup 16 Juni 2026",None,
             "19-20 Juni 2026","26 Juni 2026",
             "Info jadwal pasti: hubungi PMB UKDW. Hari 1: Tes Bahasa Inggris dan Wawancara. Hari 2: Tes Kognitif dan Tes Kepribadian."),
        ]
    )


async def _isi_beasiswa(conn):
    """
    Data beasiswa lengkap UKDW per April 2026.
    Sumber: ukdw.ac.id/beasiswa/ + ssat.ukdw.ac.id + ukdw.ac.id/pengumuman/

    Dibagi 4 kategori:
    1. mahasiswa_baru — diberikan saat PMB, biasanya berupa FREE DPFP + SPP full
    2. mahasiswa_aktif — diberikan per semester/tahun untuk mahasiswa yang sudah kuliah
    3. eksternal — dari pemerintah/lembaga luar, prosedur melalui UKDW
    4. pinjaman — bukan beasiswa, tapi bantuan cicilan tanpa bunga
    """
    await conn.executemany(
        "INSERT INTO beasiswa (nama_beasiswa,penyelenggara,kategori,jenis,sasaran,cakupan,persyaratan,cara_daftar,kontak,aktif,catatan) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        [

            ("UKDW Scholarship",
             "UKDW", "mahasiswa_baru", "full",
             "S1 kecuali Kedokteran dan Teologi/Filsafat Keilahian",
             "FREE: DPFP, SPP Tetap dan Variabel (semester reguler), asuransi kesehatan, biaya SKS Tugas Akhir, biaya daftar wisuda, iuran kegiatan mahasiswa, layanan internet. Berlaku 8 semester (tidak berlaku saat KAS). IPK minimal 3,0 untuk perpanjangan.",
             "Calon mahasiswa baru berprestasi akademik atau non-akademik luar biasa (juara/medali tingkat kabupaten, provinsi, nasional, atau internasional). Mendaftar lewat seleksi UKDW Scholarship saat PMB.",
             "Mendaftar melalui seleksi UKDW Scholarship di pmb.ukdw.ac.id saat penerimaan mahasiswa baru.",
             "PMB UKDW: pmb@ukdw.ac.id",
             True,
             "Beasiswa kompetitif bergengsi UKDW. Seleksi terpisah dari jalur PMB reguler."),

            ("Beasiswa Talenta Duta Wacana",
             "UKDW", "mahasiswa_baru", "full",
             "S1 kecuali Kedokteran",
             "FREE: SPP, DPFP, SKS Tugas Akhir, biaya daftar wisuda, iuran kegiatan mahasiswa, asuransi, layanan internet terintegrasi. Berlaku 8 semester (tidak berlaku saat KAS). IPK minimal 3,0 untuk perpanjangan.",
             "Calon mahasiswa baru dari keluarga tidak mampu (dibuktikan dokumen ekonomi). Seleksi PMB.",
             "Mendaftar melalui seleksi beasiswa Talenta saat PMB di pmb.ukdw.ac.id.",
             "PMB UKDW: pmb@ukdw.ac.id",
             True,
             "Beasiswa full untuk mahasiswa kurang mampu berprestasi. Dicek status di ssat.ukdw.ac.id > Keuangan > Beasiswa."),

            ("Beasiswa Samapta",
             "UKDW", "mahasiswa_baru", "full",
             "S1 kecuali Kedokteran",
             "FREE: SPP, DPFP, dan fasilitas akademik selama 8 semester (tidak berlaku KAS). IPK minimal 3,0.",
             "Khusus putra/putri prajurit TNI-AD di lingkup Korem 072 Pamungkas (Yogyakarta dan sekitarnya).",
             "Mendaftar melalui jalur PMB khusus Samapta. Hubungi PMB atau Biro 3 untuk info prosedur.",
             "PMB UKDW: pmb@ukdw.ac.id | Biro 3 UKDW:",
             True,
             "Kerjasama UKDW dengan Korem 072 Pamungkas untuk pendidikan keluarga TNI-AD."),

            ("Beasiswa Afirmasi Pendidikan Tinggi (ADiK)",
             "Pemerintah (Kemendikbudristek)", "mahasiswa_baru", "full",
             "S1 kecuali Kedokteran dan Teologi/Filsafat Keilahian",
             "FREE: DPFP, SPP Tetap dan Variabel, SKS Tugas Akhir, biaya wisuda, iuran kegiatan, asuransi, layanan internet. Berlaku 8 semester.",
             "KHUSUS calon mahasiswa baru asal Papua, Papua Barat, Anak TKI, dan asal Daerah 3T (Terdepan, Terluar, Tertinggal).",
             "Mendaftar melalui program ADiK Kemendikbudristek saat PMB. Hubungi PMB UKDW untuk prosedur.",
             "PMB UKDW: pmb@ukdw.ac.id | Kemendikbudristek",
             True,
             "Program afirmasi pemerintah. Terbatas kuota. Proses di luar jalur PMB reguler."),


            ("Beasiswa Kebutuhan",
             "UKDW (Biro 3)", "mahasiswa_aktif", "sebagian",
             "S1 kecuali Kedokteran",
             "Subsidi potongan SPP Variabel/SKS maksimal Rp 600.000 per semester.",
             "Mahasiswa aktif S1 UKDW (kecuali Kedokteran) yang mengalami kesulitan ekonomi sesuai standar KIP-K. Tidak berlaku saat KAS.",
             "Pengajuan via link yang dibuka oleh Biro 3 setiap awal semester. Cek di ssat.ukdw.ac.id atau Instagram @biro3ukdw.",
             "Biro 3 UKDW",
             True,
             "Plafon maksimal per mahasiswa per semester Rp 600.000. Dicek status di ssat.ukdw.ac.id."),

            ("Beasiswa Bank BPD DIY",
             "Bank BPD DIY", "mahasiswa_aktif", "sebagian",
             "S1 kecuali Kedokteran",
             "Subsidi potongan biaya pendidikan. Besaran sesuai kontrak per tahun. Tidak berlaku saat KAS.",
             "Mahasiswa aktif S1 UKDW (kecuali Kedokteran) kurang mampu sesuai standar KIP-K.",
             "Pengajuan melalui Biro 3 UKDW. Kontrak per tahun.",
             "Biro 3 UKDW | Bank BPD DIY",
             True,
             "Kerjasama UKDW dengan Bank BPD DIY untuk membantu mahasiswa kurang mampu."),

            ("Beasiswa Prestasi Akademik Mahasiswa",
             "UKDW (Biro 3)", "mahasiswa_aktif", "sebagian",
             "S1 semua prodi",
             "Subsidi potongan SPP Variabel/SKS maksimal Rp 1.500.000 per semester. Diberikan kepada 3 mahasiswa IPK tertinggi per prodi.",
             "IPK minimal 3,25. Diambil 3 orang per prodi per semester dengan IPK tertinggi. Tidak berlaku saat KAS & Kedokteran.",
             "Pengajuan via link yang dibuka Biro 3 setiap awal semester. Cek pengumuman di ssat.ukdw.ac.id.",
             "Biro 3 UKDW",
             True,
             "Otomatis diidentifikasi berdasarkan IPK tertinggi per prodi setiap semester."),

            ("Beasiswa Prestasi Umum (Seni/Olahraga/Softskill)",
             "UKDW (Biro 3)", "mahasiswa_aktif", "sebagian",
             "S1 semua prodi",
             "Subsidi potongan biaya pendidikan. Besaran sesuai tingkat kejuaraan.",
             "Mahasiswa aktif S1 yang meraih juara 1-3 tingkat kabupaten, provinsi, nasional, atau internasional di bidang seni, olahraga, atau softskill.",
             "Laporkan prestasi dan ajukan via Biro 3 UKDW dengan melampirkan sertifikat/piagam.",
             "Biro 3 UKDW",
             True,
             "Mencakup prestasi seni, olahraga, dan pengembangan diri/softskill."),

            ("Beasiswa Poin SAC Tertinggi",
             "UKDW (Biro 3)", "mahasiswa_aktif", "sebagian",
             "S1 semua prodi (1 orang per prodi)",
             "Subsidi potongan SPP Variabel/SKS sebesar Rp 2.000.000 per semester.",
             "Satu mahasiswa dengan poin SAC (Student Activity Credit) tertinggi per prodi S1 setiap semester.",
             "Otomatis diidentifikasi berdasarkan poin SAC tertinggi per prodi. Link pengajuan dibuka oleh Biro 3.",
             "Biro 3 UKDW",
             True,
             "SAC = poin keaktifan dalam kegiatan kemahasiswaan UKDW. Dicek di portal mahasiswa."),

            ("Beasiswa Anak Karyawan/Pensiunan UKDW",
             "UKDW (Biro 3)", "mahasiswa_aktif", "sebagian",
             "S1 dan Profesi",
             "Subsidi potongan SPP Variabel/SKS sesuai jumlah SKS normal per prodi.",
             "Putra/putri karyawan aktif atau pensiunan UKDW yang terdaftar sebagai mahasiswa aktif.",
             "Pengajuan via link yang dibuka Biro 3 setiap awal semester.",
             "Biro 3 UKDW",
             True,
             "Berlaku setiap semester. Cek pengumuman di ssat.ukdw.ac.id."),

            ("Beasiswa ADARO",
             "Adaro Foundation / PT Adaro Indonesia", "mahasiswa_aktif", "sebagian",
             "S1 Arsitektur, Desain Produk, Informatika, Sistem Informasi, Biologi",
             "Beasiswa biaya pendidikan (besaran sesuai kebijakan Adaro per tahun). Berlaku bagi mahasiswa aktif semester 3-7.",
             "Mahasiswa aktif S1 UKDW prodi: Arsitektur, Desain Produk, Informatika, Sistem Informasi, atau Biologi. Semester 3-7. Kuota ±25 orang per tahun. Penerima tahun sebelumnya diprioritaskan.",
             "Download form pendaftaran dari link yang diumumkan Biro 3. Isi dengan tulisan tangan, lengkapi berkas, scan dan upload via link yang disediakan.",
             "Biro 3 UKDW",
             True,
             "Beasiswa korporasi dari Adaro Foundation. Pengumuman penerimaan via ssat.ukdw.ac.id dan Biro 3."),

            ("Beasiswa Scranton",
             "Scranton University, Korea Selatan", "mahasiswa_aktif", "full",
             "S1 & S2 (Pascasarjana S2 & S3), kecuali Kedokteran",
             "Coverage biaya kuliah penuh selama 1-2 semester atau 1 tahun akademik (sesuai program). Untuk pemenang Scranton Essay & Film Contest.",
             "KHUSUS MAHASISWI (perempuan). Ikuti Scranton Essay & Film Contest yang diadakan UKDW. Peserta terbaik mendapatkan beasiswa.",
             "Ikuti kompetisi Essay & Film Contest Scranton yang diumumkan UKDW setiap tahun. Pemenang otomatis menerima beasiswa.",
             "Biro 3 UKDW",
             True,
             "Beasiswa dari Scranton University (Korea). Khusus mahasiswi. Pemenang 2025 mendapat free biaya kuliah penuh semester Gasal & Genap 2025/2026."),

            ("Beasiswa GKI Pondok Indah & GKI Kebayoran Baru",
             "GKI Pondok Indah & GKI Kebayoran Baru", "mahasiswa_aktif", "sebagian",
             "S1 — khusus Fakultas Teologi",
             "Potongan biaya pendidikan (besaran sesuai kebijakan gereja).",
             "Mahasiswa aktif S1 Fakultas Teologi UKDW. Anggota jemaat GKI Pondok Indah atau GKI Kebayoran Baru.",
             "Melalui gereja asal masing-masing. Dikomunikasikan ke Biro 3 UKDW.",
             "Biro 3 UKDW",
             True, "Khusus mahasiswa Fakultas Teologi dari jemaat GKI terkait."),

            ("Beasiswa Sinode GKJW, Sinode GKJ, Sinode GKP",
             "GKJW / GKJ / GKP", "mahasiswa_aktif", "sebagian",
             "S1 — khusus Fakultas Teologi",
             "Potongan biaya pendidikan (besaran sesuai kebijakan sinode).",
             "Mahasiswa aktif S1 Fakultas Teologi UKDW. Anggota jemaat GKJW, GKJ, atau GKP.",
             "Melalui sinode gereja asal masing-masing. Dikomunikasikan ke Biro 3 UKDW.",
             "Biro 3 UKDW",
             True, "GKJW = Gereja Kristen Jawi Wetan. GKJ = Gereja Kristen Jawa. GKP = Gereja Kristen Pasundan."),

            ("Beasiswa BNI",
             "Bank BNI", "mahasiswa_aktif", "sebagian",
             "S1 tertentu (sesuai kebijakan BNI)",
             "Subsidi biaya pendidikan (besaran sesuai kebijakan BNI per program).",
             "Mahasiswa aktif UKDW yang memenuhi syarat BNI. Syarat detil sesuai program BNI yang berjalan.",
             "Melalui Biro 3 UKDW. Pengumuman disampaikan saat program BNI aktif.",
             "Biro 3 UKDW | Bank BNI UKDW",
             True, None),


            ("KIP-Kuliah Merdeka",
             "Pemerintah (Kemendikbudristek)", "eksternal", "full",
             "S1 kecuali Kedokteran",
             "Full biaya pendidikan (DPFP + SPP sesuai besaran Kemendikbud) + uang saku/biaya hidup. Berlaku sampai selesai studi (maks 8 semester).",
             "Terdaftar dalam DTKS, memiliki KIP Sekolah/PKH/KKS atau dari keluarga tidak mampu. Mendaftar saat PMB. IPK minimal 2,75 untuk perpanjangan.",
             "Daftar di kipkuliah.kemdikbud.go.id saat proses PMB. Pilih UKDW sebagai perguruan tinggi tujuan.",
             "KIP-Kuliah: kipkuliah@kemdikbud.go.id | PMB UKDW: pmb@ukdw.ac.id",
             True,
             "Salah satu beasiswa pemerintah terbesar Indonesia. Cek status di ssat.ukdw.ac.id > Keuangan > Beasiswa."),

            ("LPDP (Lembaga Pengelola Dana Pendidikan)",
             "Pemerintah (LPDP Kemenkeu)", "eksternal", "full",
             "S2 dan S3",
             "Full biaya pendidikan + biaya hidup bulanan + biaya penelitian/tesis + tiket. Berlaku selama masa studi.",
             "WNI, sudah diterima di universitas tujuan (UKDW termasuk), usia < 35 tahun (S2). Tidak sedang menerima beasiswa pemerintah lain.",
             "Mendaftar di beasiswa.lpdp.kemenkeu.go.id secara mandiri. Koordinasi surat penerimaan dengan PMB UKDW.",
             "LPDP: lpdp@kemenkeu.go.id | pmb@ukdw.ac.id",
             True,
             "Beasiswa bergengsi pemerintah Indonesia. UKDW terdaftar sebagai PT penerima LPDP."),

            ("Beasiswa Djarum Foundation",
             "Djarum Foundation", "eksternal", "sebagian",
             "S1 berbagai prodi",
             "Beasiswa pendidikan + program pengembangan diri (kepemimpinan, workshop, coaching). Besaran sesuai kebijakan Djarum.",
             "IPK minimal 3,20, aktif berorganisasi, tidak sedang menerima beasiswa lain. Mahasiswa aktif semester 3 ke atas.",
             "Melalui website Djarum Beasiswa Plus. Dikomunikasikan ke Biro 3 UKDW.",
             "beasiswaplus.djarum.com | Biro 3 UKDW",
             True,
             "UKDW memiliki kerjasama resmi dengan Djarum Foundation."),


            ("Pinjaman Registrasi",
             "UKDW (Biro 3)", "pinjaman", "pinjaman",
             "S1 dan Profesi Dokter",
             "Cicilan maksimal 75% biaya SKS Variabel per semester. TANPA BUNGA. Jangka waktu cicilan disepakati bersama Biro 3.",
             "Mahasiswa aktif S1 atau Profesi Dokter UKDW yang mengalami kesulitan pembayaran registrasi semester.",
             "Isi formulir Pinjaman Registrasi di Biro 3. Link pengajuan dibuka oleh Biro 3 setiap awal semester (biasanya akhir Januari untuk semester Genap, awal Agustus untuk semester Ganjil).",
             "Biro 3 UKDW — datang langsung atau WA: 0812 2823 6737",
             True,
             "TIDAK berbunga. Pengumuman penerima bisa dicek di ssat.ukdw.ac.id > Tagihan > Invoice > Cetak Invoice."),
        ]
    )


async def _isi_pertukaran_mahasiswa(conn):
    """
    Program pertukaran mahasiswa lengkap dari OIA UKDW.
    Diperbarui: April 2026
    Sumber : ukdw.ac.id/en/oia/  |  ukdw.ac.id/en/category/program-oia/outbound-program/
             ukdw.ac.id/en/2024/01/11/inbound-mobility-programs/

    Kontak OIA UKDW:
        Gedung Hagios Lantai 1
        Tel  : +62 274 563929 ext. 118/324
        Email: oia@staff.ukdw.ac.id
        Daftar outbound : bit.ly/appOutboundUKDW
        WhatsApp OIA    : bit.ly/askOIA

    Urutan penyimpanan: program TERBARU (tanggal_mulai terbesar) ditaruh lebih dulu.
    Retrieval akan ORDER BY tanggal_mulai DESC NULLS LAST sehingga data paling
    baru selalu muncul di atas; jika kosong/NULL, program recurring tanpa tanggal
    tetap tersedia sebagai fallback.

    Kolom tuple:
        $1  nama_program         $2  tanggal_mulai        $3  tanggal_selesai
        $4  tanggal_pendaftaran  $5  universitas_mitra    $6  negara
        $7  kategori             $8  jenis_program        $9  durasi
        $10 persyaratan          $11 pendanaan            $12 kontak
        $13 deskripsi
    """
    INSERT = (
        "INSERT INTO pertukaran_mahasiswa "
        "(nama_program,tanggal_mulai,tanggal_selesai,tanggal_pendaftaran,"
        "universitas_mitra,negara,kategori,jenis_program,durasi,"
        "persyaratan,pendanaan,kontak,deskripsi) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)"
    )
    await conn.executemany(INSERT, [


        (
            "2026 K-SAIL Korean Language Program",
            "2026-03-03, 2026-06-01",
            "2026-05-12, 2026-08-07",
            "2026-01-19",
            "Korea Maritime and Ocean University (한국해양대학교)", "Korea Selatan",
            "outbound", "student_exchange",
            "1 semester (Maret-Juni 2026) atau 1 semester (Juni-Agustus 2026)",
            "KTM, copy paspor, foto background solid, transkrip nilai Bahasa Inggris, "
            "motivation letter, financial statement, nomor WA aktif. Ajukan ke OIA UKDW.",
            "Biaya hidup dan akomodasi ditanggung mahasiswa. SPP UKDW tetap dibayar. "
            "Biaya hidup ditanggung mahasiswa.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program exchange Spring 2026 di Korea Maritime and Ocean University (KMOU), Korea Selatan — "
            "universitas dengan fokus studi kelautan dan budaya Korea. "
            "Proses: seleksi internal OIA UKDW → nominasi ke KMOU → seleksi final KMOU. "
            "Info lengkap: https://ukdw.ac.id/en/2026/01/07/2026-k-sail-korean-language-program/"
        ),
        (
            "KMOU OceanX - International Summer School 2026",
            "2026-07-06",
            "2026-07-24",
            "2026-04-23",
            "Korea Maritime and Ocean University (한국해양대학교)", "Korea Selatan",
            "outbound", "student_exchange",
            "18 Hari (6-24 Juli 2026)",
            "Mahasiswa aktif UKDW. Pilih departemen sesuai latar belakang akademik. "
            "Tersedia kursus Bahasa Korea (3 SKS). Ajukan ke OIA UKDW.",
            "Biaya hidup ditanggung mahasiswa. SPP UKDW tetap dibayar. "
            "GKS (Global Korea Scholarship) oleh NIIED mungkin tersedia — tidak dijamin.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program exchange Summer 2026 di Korea Maritime and Ocean University (KMOU). "
            "Pengumuman dibuka semester genap 2025/2026. Fokus pada studi kelautan, teknik, dan budaya Korea. "
            "Info: https://ukdw.ac.id/en/2026/04/16/kmou-oceanx-international-summer-school-2026/"
        ),
        (
            "Student Exchange Spring 2025 — Handong Global University (HGU)",
            "2025-03-01",
            "2025-06-30",
            "2024-11-01",
            "Handong Global University (한동대학교)", "Korea Selatan",
            "outbound", "student_exchange",
            "1 semester (Spring 2025)",
            "KTM, copy paspor, foto background solid, transkrip nilai Bahasa Inggris, "
            "motivation letter, financial statement, nomor WA aktif. Ajukan ke OIA UKDW.",
            "Biaya hidup dan akomodasi ditanggung mahasiswa. SPP UKDW tetap dibayar. "
            "Beasiswa parsial dari HGU mungkin tersedia.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program exchange Spring 2025 di Handong Global University (HGU), Korea Selatan — "
            "universitas Kristen multibahasa (Korea & Inggris). Pengumuman dibuka 2 Oktober 2024. "
            "Proses: seleksi internal OIA UKDW → nominasi ke HGU → seleksi final HGU. "
            "Info lengkap: ukdw.ac.id/en/2024/10/02/spring-2025-student-exchange-program-at-"
            "handong-global-university-korea/"
        ),
        (
            "Student Exchange Spring 2025 — I-Shou University",
            "2025-02-01",
            "2025-06-30",
            "2024-11-01",
            "I-Shou University (義守大學)", "Taiwan",
            "outbound", "student_exchange",
            "1 semester (Spring 2025)",
            "KTM, transkrip nilai Bahasa Inggris, copy paspor, copy KTP, foto background solid, "
            "motivation letter/study plan Bahasa Inggris, financial statement, language proficiency. "
            "Ajukan ke OIA UKDW.",
            "Biaya hidup dan akomodasi ditanggung mahasiswa. SPP UKDW tetap dibayar.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program exchange Spring 2025 di I-Shou University, Kaohsiung, Taiwan. "
            "Pengumuman dibuka 1 Oktober 2024. "
            "Info: ukdw.ac.id/en/2024/10/01/student-exchange-program-at-i-shou-university-taiwan/"
        ),

        (
            "Tunghai Language and Culture Summer Program 2024",
            "2024-07-07",
            "2024-07-27",
            "2024-06-01",
            "Tunghai University (東海大學)", "Taiwan",
            "outbound", "short_term",
            "3 minggu (7-27 Juli 2024)",
            "Mahasiswa aktif UKDW. Mendaftar melalui OIA UKDW.",
            "Biaya ditanggung peserta. Detail biaya dari Tunghai University.",
            "OIA UKDW — bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program bahasa Mandarin dan budaya Taiwan musim panas di Tunghai University, "
            "Taichung City. Pengumuman: 15 Januari 2024. Berlangsung 7-27 Juli 2024."
        ),
        (
            "ACUCA Student Camp 2026",
            "2026-08-17", 
            "2026-08-21",
            "2026-05-14",
            "Association of Christian Universities and Colleges in Asia (ACUCA)",
            "Berganti per tahun",
            "outbound", "short_term",
            "4-5 hari",
            "Mahasiswa aktif UKDW. Mengirimkan CV, pasfoto, fotokopi passport, fotokopi KTM, fotokopi KTP, motivation letter, transkrip nilai dalam bahasa inggris, Surat rekomendasi.",
            "Biaya bervariasi (sebagian ditanggung UKDW).",
            "OIA UKDW — bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Kegiatan tahunan asosiasi universitas Kristen se-Asia. UKDW sebagai anggota ACUCA "
            "mengirimkan delegasi mahasiswa untuk mengikuti student camp internasional."
        ),
        (
            "ACUCA MDP Winter 2026",
            "2026-01-26", 
            "2026-02-13",
            "2025-12-12",
            "Association of Christian Universities and Colleges in Asia (ACUCA)",
            "Berganti per tahun",
            "outbound", "short_term",
            "18 hari",
            "Mahasiswa aktif UKDW. Mengirimkan CV, pasfoto, fotokopi passport, fotokopi KTM, fotokopi KTP, motivation letter, transkrip nilai dalam bahasa inggris, Surat rekomendasi.",
            "Biaya bervariasi (sebagian ditanggung UKDW).",
            "OIA UKDW — bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Kegiatan tahunan asosiasi universitas Kristen se-Asia. UKDW sebagai anggota ACUCA "
            "mengirimkan delegasi mahasiswa untuk mengikuti student camp internasional."
        ),
        (
            "GlobEEs — Global Education Experiences 2026",
            "2026-05-03",
            "2026-05-16",
            "2026-02-05",
            "Chang Jung Christian University (CJCU) / UKSW", "Indonesia & Taiwan",
            "outbound", "short_term",
            "13 hari (3-16 Mei 2026, onsite di Taiwan)",
            "Mahasiswa aktif UKDW. Lancar komunikasi dalam bahasa Inggris. Minimal IPK 3.0.",
            "Biaya bervariasi. Beberapa sesi online gratis/bersubsidi.",
            "OIA UKDW — bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program kolaborasi UKDW, Chang Jung Christian University (CJCU) Taiwan, dan UKSW. "
            "Edisi 2026 dilaksanakan di Indonesia (3-16 Mei 2026). "
            "Fokus budaya, akademik, dan pengembangan diri secara internasional."
        ),


        (
            "Student Exchange — Goshen College (Outbound)",
            None, None, None,
            "Goshen College", "Amerika Serikat",
            "outbound", "student_exchange",
            "1 semester",
            "KTM, transkrip nilai Bahasa Inggris, copy paspor, surat rekomendasi fakultas, "
            "motivation letter. Konsultasi ke OIA UKDW.",
            "Tergantung program dan MoU. Beberapa biaya mungkin ditanggung sesuai perjanjian.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program pertukaran ke Goshen College, Indiana, USA — universitas Kristen Mennonit, "
            "salah satu mitra historis terlama UKDW di Amerika Serikat."
        ),
        (
            "Student Exchange — Tunghai University (Reguler)",
            None, None, None,
            "Tunghai University (東海大學)", "Taiwan",
            "outbound", "student_exchange",
            "1 semester",
            "KTM, transkrip nilai Bahasa Inggris, copy paspor, motivation letter, "
            "financial statement. Ajukan ke OIA UKDW.",
            "Biaya hidup ditanggung mahasiswa. SPP UKDW tetap dibayar.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program exchange reguler semester di Tunghai University, Taichung City, Taiwan — "
            "salah satu universitas Kristen terkemuka di Taiwan. Buka tiap semester."
        ),
        (
            "Student Exchange — Ouachita Baptist University",
            None, None, None,
            "Ouachita Baptist University", "Amerika Serikat",
            "outbound", "student_exchange",
            "1 semester",
            "KTM, transkrip nilai, copy paspor, surat rekomendasi, motivation letter. "
            "Konsultasi ke OIA UKDW.",
            "Tergantung program dan MoU.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program pertukaran dengan Ouachita Baptist University, Arkansas, USA — "
            "universitas Kristen Baptist."
        ),
        (
            "Student Exchange — Philippine Normal University",
            None, None, None,
            "Philippine Normal University", "Filipina",
            "outbound", "student_exchange",
            "1 semester",
            "KTM, transkrip nilai, copy paspor, surat rekomendasi, motivation letter. "
            "Konsultasi ke OIA UKDW.",
            "Tergantung program.",
            "OIA UKDW — bit.ly/appOutboundUKDW | bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program pertukaran dengan Philippine Normal University — universitas terkemuka "
            "di Filipina, khususnya bidang pendidikan."
        ),
        (
            "Hanseo Global Leadership Program",
            None, None, None,
            "Hanseo University (한서대학교)", "Korea Selatan",
            "outbound", "short_term",
            "Beberapa minggu",
            "Mahasiswa aktif UKDW. Syarat detail diumumkan OIA saat program dibuka.",
            "Biaya ditanggung mahasiswa (sebagian mungkin disubsidi UKDW).",
            "OIA UKDW — bit.ly/askOIA | oia@staff.ukdw.ac.id",
            "Program kepemimpinan internasional bersama Hanseo University, Korea Selatan. "
            "Fokus pengembangan jiwa kepemimpinan, budaya Korea, dan networking internasional."
        ),


        (
            "International Semester Programme 2026 — EvH Bochum",
            "2026-04-06",
            "2026-07-31",
            "2025-12-01",
            "Protestant University of Applied Sciences (EvH) Bochum", "Jerman",
            "inbound", "inbound_short_term",
            "Sekitar 3,5 bulan (April-Juli 2026)",
            "Mahasiswa internasional dari universitas mitra UKDW. "
            "Ajukan aplikasi melalui OIA atau universitas asal.",
            "Tergantung program dan MoU universitas asal.",
            "OIA UKDW — oia@staff.ukdw.ac.id | Tel: +62 274 563929 ext. 118/324",
            "International Semester Programme 2026 bertema 'Social Complexities and Creative "
            "Approaches: Diversity, Disability, Sustainability'. Dilaksanakan di UKDW, "
            "Yogyakarta, 6 April-31 Juli 2026. Kolaborasi dengan EvH Bochum, Jerman. "
            "Info: ukdw.ac.id/en/oia/"
        ),

        (
            "Goshen College SST (Study-Service Term) — Inbound ke UKDW",
            None, None, None,
            "Goshen College", "Amerika Serikat",
            "inbound", "inbound_exchange",
            "6 minggu (bagian dari 1 semester SST Goshen)",
            "Mahasiswa Goshen College yang terdaftar dalam program SST Indonesia. "
            "Pendaftaran melalui Goshen College, bukan langsung ke UKDW.",
            "Ditanggung program SST Goshen College.",
            "OIA UKDW — oia@staff.ukdw.ac.id | Tel: +62 274 563929 ext. 118/324",
            "Program Study-Service Term (SST) Goshen College menghabiskan 6 minggu pertama "
            "di UKDW, Yogyakarta: belajar Bahasa Indonesia, budaya, sejarah, dan politik. "
            "Kunjungan lapangan ke Borobudur, Prambanan, dll. Program ini rutin setiap semester "
            "dan merupakan salah satu kemitraan inbound terkuat UKDW. "
            "Info: goshen.edu/indonesia/"
        ),
        (
            "UKDW Student Exchange Inbound — Universitas Mitra",
            None, None, None,
            "Berbagai universitas mitra UKDW", "Berbagai negara",
            "inbound", "inbound_exchange",
            "1 semester",
            "Mahasiswa asing dari universitas mitra resmi UKDW. "
            "Wajib memiliki paspor valid, visa pelajar, dan surat penerimaan dari UKDW. "
            "Ajukan aplikasi ke OIA UKDW: bit.ly/askOIA.",
            "Umumnya mahasiswa tetap membayar biaya ke universitas asal, "
            "tidak ada biaya kuliah tambahan di UKDW.",
            "OIA UKDW — oia@staff.ukdw.ac.id | Tel: +62 274 563929 ext. 118/324",
            "UKDW menerima mahasiswa internasional dari universitas mitra seluruh dunia "
            "untuk program pertukaran satu semester. Mahasiswa dapat mengikuti perkuliahan "
            "regular UKDW dan memperoleh kredit yang dapat ditransfer ke universitas asal. "
            "Info lengkap: ukdw.ac.id/en/oia/ | oia.ukdw.ac.id"
        ),
        (
            "Inbound Short-Term & Summer Program — UKDW",
            None, None, None,
            "Berbagai universitas mitra UKDW", "Berbagai negara",
            "inbound", "inbound_short_term",
            "Beberapa minggu (tergantung program)",
            "Mahasiswa internasional dari universitas mitra UKDW. "
            "Pendaftaran melalui OIA UKDW atau universitas asal.",
            "Biaya bervariasi tergantung program.",
            "OIA UKDW — oia@staff.ukdw.ac.id | Tel: +62 274 563929 ext. 118/324",
            "UKDW menyelenggarakan berbagai program inbound jangka pendek termasuk summer camp, "
            "program budaya Indonesia, kursus Bahasa Indonesia, dan collaborative courses "
            "bersama universitas mitra. Mahasiswa mendapat pengalaman budaya Yogyakarta, "
            "kunjungan ke situs bersejarah, dan interaksi dengan mahasiswa UKDW. "
            "Info: ukdw.ac.id/en/2024/01/11/inbound-mobility-programs/"
        ),
        (
            "GlobEEs — Global Education Experiences (UKDW sebagai Host)",
            None, None, None,
            "Chang Jung Christian University (CJCU) / UKSW", "Taiwan & Indonesia",
            "inbound", "inbound_short_term",
            "Sekitar 2 minggu",
            "Mahasiswa dari CJCU Taiwan dan UKSW yang datang ke UKDW. "
            "Dikoordinasikan oleh OIA UKDW.",
            "Biaya bervariasi tergantung peran peserta.",
            "OIA UKDW — oia@staff.ukdw.ac.id | bit.ly/askOIA",
            "GlobEEs adalah program kolaborasi tiga universitas Kristen: UKDW, CJCU Taiwan, "
            "dan UKSW. Ketika berlokasi di Indonesia/UKDW, program ini menjadi inbound bagi "
            "peserta dari CJCU dan UKSW. Fokus: budaya, akademik, dan pengembangan diri lintas budaya."
        ),
    ])

