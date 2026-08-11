"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     WACA — UKDW's Personal Chatbot System                    ║
║                              retrieval.py  (v3)                              ║
║              Stage 2 — SQL Retrieval (skema v3, data real UKDW)              ║
╚══════════════════════════════════════════════════════════════════════════════╝

PEMETAAN INTENT → HANDLER → TABEL UTAMA:

  Intent               Handler                      Tabel
  ─────────────────    ─────────────────────────    ─────────────────────────
  layanan_akademik  → _cari_layanan_akademik        pengetahuan (biro_1)
  kemahasiswaan     → _cari_kemahasiswaan           pengetahuan (biro_3)
  kerjasama         → _cari_kerjasama               pengetahuan (biro_4, non-exchange)
  student_exchange  → _cari_student_exchange        pertukaran_mahasiswa
                                                    + pengetahuan (biro_4)
  pendaftaran       → _cari_pendaftaran             jalur_pendaftaran
                                                    + jadwal_pendaftaran
                                                    + pengetahuan (pmb)
  biaya_kuliah      → _cari_biaya_kuliah            biaya_kuliah
  program_studi     → _cari_program_studi           program_studi
  beasiswa          → _cari_beasiswa                beasiswa
                                                    + pengetahuan (biro_3)
  general           → _cari_umum                   pengetahuan (semua unit)

PERUBAHAN v3:
  - Tabel beasiswa sekarang memiliki kolom 'kategori'
    (mahasiswa_baru / mahasiswa_aktif / eksternal / pinjaman)
    dan kolom 'cakupan' menggantikan 'nominal'
  - Tabel pertukaran_mahasiswa memiliki kolom 'kategori' (outbound/inbound)
    dan 'jenis_program' (student_exchange/short_term/iisma/inbound_exchange/inbound_short_term)
  - Retrieval menggunakan ORDER BY tanggal_mulai DESC NULLS LAST untuk menampilkan
    program terbaru terlebih dahulu; program tanpa tanggal (NULL) muncul di akhir
    → query bisa filter lebih spesifik
  - Handler student_exchange kini mendukung filter jenis_program
  - Handler beasiswa kini mendukung filter kategori beasiswa
"""

import logging
from dataset.database import get_pool

logger = logging.getLogger(__name__)


async def retrieve_data(intent: str, entities: dict) -> dict:
    """
    Dispatch ke handler yang sesuai berdasarkan intent.
    Mengembalikan: { "sql_query": str, "data": list[dict] }
    """
    dispatch = {
        "layanan_akademik":  _cari_layanan_akademik,
        "kemahasiswaan":     _cari_kemahasiswaan,
        "kerjasama":         _cari_kerjasama,
        "student_exchange":  _cari_student_exchange,
        "pendaftaran":       _cari_pendaftaran,
        "biaya_kuliah":      _cari_biaya_kuliah,
        "program_studi":     _cari_program_studi,
        "beasiswa":          _cari_beasiswa,
        "general":           _cari_umum,
    }
    handler = dispatch.get(intent, _cari_umum)
    try:
        return await handler(entities)
    except Exception as e:
        logger.error(f"[SQL] Retrieval error untuk intent='{intent}': {e}")
        return {"sql_query": "", "data": []}


def _build_keyword_filter(keyword: str, params: list, cols: list[str]) -> str:
    """
    Bangun kondisi SQL untuk keyword search yang robust.

    Masalah: LLM kadang mengirim frasa panjang seperti "prosedur kerjasama perusahaan"
    sehingga ILIKE '%prosedur kerjasama perusahaan%' gagal karena token di DB
    disimpan terpisah ("kerjasama perusahaan fakultas surat prosedur biro 4").

    Solusi: pecah keyword menjadi token individu (≥3 karakter), lalu gabungkan
    dengan OR — setiap token dicek terhadap semua kolom target.
    Jika ada token exact di kategori → langsung return kondisi kategori saja (lebih presisi).

    Returns SQL condition string (tanpa WHERE/AND prefix).
    """
    if not keyword:
        return ""

    tokens = [t.strip().lower() for t in keyword.replace(",", " ").split() if len(t.strip()) >= 3]
    if not tokens:
        return ""

    conditions = []
    for token in tokens:
        params.append(f"%{token}%")
        idx = len(params)
        col_conditions = " OR ".join(
            f"LOWER({col}) ILIKE LOWER(${idx})" for col in cols
        )
        conditions.append(f"({col_conditions})")

    return "(" + " OR ".join(conditions) + ")"



async def _cari_pengetahuan_full(
    keyword: str = None,
    unit_kerja: str = None,
    kategori: str = None,
    exclude_kategori: str = None
) -> dict:
    """
    Pencarian fleksibel di tabel pengetahuan.
    Mendukung filter: unit_kerja, kategori, exclude_kategori, keyword.

    Keyword di-match ke kolom: kata_kunci, pertanyaan, jawaban secara bersamaan
    menggunakan OR sehingga hasil lebih relevan.
    """
    pool = await get_pool()
    conditions = []
    params = []

    if unit_kerja:
        params.append(unit_kerja)
        conditions.append(f"unit_kerja = ${len(params)}")

    if kategori:
        params.append(f"%{kategori}%")
        conditions.append(f"LOWER(kategori) ILIKE LOWER(${len(params)})")

    if exclude_kategori:
        params.append(f"%{exclude_kategori}%")
        conditions.append(f"LOWER(kategori) NOT ILIKE LOWER(${len(params)})")

    if keyword:
        kw_cond = _build_keyword_filter(
            keyword, params, ["kata_kunci", "pertanyaan", "jawaban", "kategori"]
        )
        if kw_cond:
            conditions.append(kw_cond)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
        SELECT unit_kerja, kategori, pertanyaan, jawaban
        FROM pengetahuan
        {where}
        ORDER BY kategori, id
        LIMIT 6
    """.strip()

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return {"sql_query": sql, "data": [dict(r) for r in rows]}


async def _cari_layanan_akademik(entities: dict) -> dict:
    """
    Mencari informasi layanan akademik mahasiswa dari Biro 1.

    Strategi keyword:
      keyword/topik → pencarian teks bebas di kata_kunci, pertanyaan, jawaban
      kategori      → DIGABUNG ke keyword (bukan filter kolom).
                      Alasan: LLM sering mengisi kategori dengan topik seperti "KRS",
                      "transkrip" yang bukan nilai kolom kategori di DB. Menggabungkan
                      keduanya ke keyword search menghasilkan match yang lebih akurat.

    Fallback bertingkat:
      1. Cari dengan keyword lengkap (keyword + kategori) → unit biro_1
      2. Jika 0 hasil, cari hanya dengan keyword → unit biro_1
      3. Jika 0 hasil, cari keyword di semua unit (general fallback)
    """
    keyword  = entities.get("keyword") or entities.get("topik") or ""
    kategori = entities.get("kategori") or ""

    combined_kw = " ".join(filter(None, [keyword, kategori])).strip()

    hasil = await _cari_pengetahuan_full(
        keyword=combined_kw, unit_kerja="biro_1"
    )

    if not hasil["data"] and combined_kw != keyword and keyword:
        hasil = await _cari_pengetahuan_full(
            keyword=keyword, unit_kerja="biro_1"
        )

    if not hasil["data"] and combined_kw:
        logger.info(f"[SQL] layanan_akademik fallback ke semua unit: keyword='{combined_kw}'")
        hasil = await _cari_pengetahuan_full(keyword=combined_kw)

    return hasil


async def _cari_kemahasiswaan(entities: dict) -> dict:
    """
    Mencari informasi kemahasiswaan, karir, organisasi, dan alumni dari Biro 3.
    Kategori entity digabung ke keyword (bukan filter kolom) — konsisten dengan
    pendekatan di _cari_layanan_akademik.
    """
    keyword  = entities.get("keyword") or entities.get("topik") or ""
    kategori = entities.get("kategori") or ""
    combined_kw = " ".join(filter(None, [keyword, kategori])).strip()

    beasiswa_keywords = ["beasiswa", "kip", "talenta", "scholarship", "stipend"]
    is_beasiswa = combined_kw and any(bw in combined_kw.lower() for bw in beasiswa_keywords)

    if is_beasiswa:
        return await _cari_pengetahuan_full(
            keyword=combined_kw, unit_kerja="biro_3"
        )

    hasil = await _cari_pengetahuan_full(
        keyword=combined_kw, unit_kerja="biro_3"
    )

    if not hasil["data"] and combined_kw:
        logger.info(f"[SQL] kemahasiswaan fallback ke semua unit: keyword='{combined_kw}'")
        hasil = await _cari_pengetahuan_full(keyword=combined_kw)

    return hasil


async def _cari_kerjasama(entities: dict) -> dict:
    """
    Mencari informasi kerjasama perusahaan, MoU, iklan, baliho dari Biro 4.
    Mengecualikan kategori 'student_exchange' (ditangani handler tersendiri).
    """
    keyword  = entities.get("keyword") or entities.get("topik")
    kategori = entities.get("kategori")

    pool = await get_pool()
    params: list = ["biro_4", "%student_exchange%"]
    extra = ""

    if keyword:
        kw_cond = _build_keyword_filter(
            keyword, params, ["kata_kunci", "pertanyaan", "jawaban"]
        )
        if kw_cond:
            extra += f" AND {kw_cond}"

    if kategori:
        params.append(f"%{kategori}%")
        extra += f" AND LOWER(kategori) ILIKE LOWER(${len(params)})"

    sql = f"""
        SELECT unit_kerja, kategori, pertanyaan, jawaban
        FROM pengetahuan
        WHERE unit_kerja = $1
          AND LOWER(kategori) NOT ILIKE $2
          {extra}
        ORDER BY kategori, id
        LIMIT 5
    """.strip()

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return {"sql_query": sql, "data": [dict(r) for r in rows]}


async def _cari_student_exchange(entities: dict) -> dict:
    """
    Menggabungkan hasil dari dua sumber:
    1. Tabel pertukaran_mahasiswa — data terstruktur program exchange
    2. Tabel pengetahuan biro_4 — Q&A prosedural tentang exchange

    Filter yang didukung:
      negara          → filter kolom negara (mis. "Korea Selatan", "Taiwan")
      universitas_mitra → filter nama universitas
      kategori        → 'outbound' (keluar negeri) / 'inbound' (mahasiswa asing ke UKDW)
      jenis_program   → student_exchange / short_term / iisma / inbound_exchange / inbound_short_term
      keyword/topik   → pencarian bebas pada nama_program, deskripsi, dst.

    Urutan hasil:
      Program dengan tanggal_mulai terbaru (paling baru) tampil pertama.
      Program tanpa tanggal (recurring/umum) tampil di akhir.
    """
    pool = await get_pool()

    negara        = entities.get("negara")
    universitas   = entities.get("universitas_mitra")
    jenis_program = entities.get("jenis_program")
    kategori      = entities.get("kategori")
    keyword       = entities.get("keyword") or entities.get("topik")

    conditions_pm: list[str] = []
    params_pm: list = []

    if negara:
        params_pm.append(f"%{negara}%")
        conditions_pm.append(f"LOWER(negara) ILIKE LOWER(${len(params_pm)})")

    if universitas:
        params_pm.append(f"%{universitas}%")
        conditions_pm.append(f"LOWER(universitas_mitra) ILIKE LOWER(${len(params_pm)})")

    if kategori:
        params_pm.append(f"%{kategori}%")
        conditions_pm.append(f"LOWER(kategori) ILIKE LOWER(${len(params_pm)})")

    if jenis_program:
        params_pm.append(f"%{jenis_program}%")
        conditions_pm.append(f"LOWER(jenis_program) ILIKE LOWER(${len(params_pm)})")

    if keyword:
        kw_cond = _build_keyword_filter(
            keyword, params_pm,
            ["nama_program", "universitas_mitra", "negara", "deskripsi"]
        )
        if kw_cond:
            conditions_pm.append(kw_cond)

    where_pm = "WHERE " + " AND ".join(conditions_pm) if conditions_pm else ""
    sql_pm = f"""
        SELECT nama_program, tanggal_mulai, tanggal_selesai, tanggal_pendaftaran,
               universitas_mitra, negara, kategori, jenis_program,
               durasi, persyaratan, pendanaan, kontak, deskripsi
        FROM pertukaran_mahasiswa
        {where_pm}
        ORDER BY
            -- Program dengan tanggal konkret (terbaru dulu)
            CASE WHEN tanggal_mulai IS NOT NULL THEN 0 ELSE 1 END,
            tanggal_mulai DESC NULLS LAST,
            -- Fallback: outbound sebelum inbound, lalu alphabetical
            CASE kategori WHEN 'outbound' THEN 0 ELSE 1 END,
            nama_program
        LIMIT 8
    """.strip()

    async with pool.acquire() as conn:
        rows_pm = await conn.fetch(sql_pm, *params_pm)

    kw_cond_pk = f"%{keyword}%" if keyword else "%exchange%"
    sql_pk = """
        SELECT kategori, pertanyaan, jawaban
        FROM pengetahuan
        WHERE unit_kerja = 'biro_4'
          AND LOWER(kategori) ILIKE '%student_exchange%'
          AND (LOWER(kata_kunci) ILIKE $1 OR LOWER(pertanyaan) ILIKE $1)
        ORDER BY id
        LIMIT 4
    """.strip()

    async with pool.acquire() as conn:
        rows_pk = await conn.fetch(sql_pk, kw_cond_pk)

    data_gabung = [dict(r) for r in rows_pm] + [dict(r) for r in rows_pk]
    return {
        "sql_query": f"-- pertukaran_mahasiswa:\n{sql_pm}\n\n-- pengetahuan biro_4:\n{sql_pk}",
        "data": data_gabung
    }


async def _cari_pendaftaran(entities: dict) -> dict:
    """
    Menggabungkan data dari tiga sumber:
    1. jalur_pendaftaran  — syarat, dokumen, biaya daftar per jalur seleksi
    2. jadwal_pendaftaran — gelombang & tanggal per jalur
    3. pengetahuan pmb    — Q&A prosedural PMB

    Filter: jalur (mis. "Seleksi Prestasi"), keyword, nama_prodi
    """
    pool = await get_pool()

    nama_jalur = entities.get("jalur") or entities.get("nama_jalur")
    keyword    = entities.get("keyword") or entities.get("topik")
    nama_prodi = entities.get("nama_prodi")

    conditions_j: list[str] = []
    params_j: list = []

    if nama_jalur:
        params_j.append(f"%{nama_jalur}%")
        conditions_j.append(f"LOWER(nama_jalur) ILIKE LOWER(${len(params_j)})")

    if nama_prodi:
        params_j.append(f"%{nama_prodi}%")
        conditions_j.append(
            f"(LOWER(nama_jalur) ILIKE LOWER(${len(params_j)}) OR "
            f" LOWER(berlaku_untuk) ILIKE LOWER(${len(params_j)}))"
        )

    if keyword:
        kw_cond = _build_keyword_filter(
            keyword, params_j,
            ["nama_jalur", "syarat_utama", "deskripsi", "catatan_khusus"]
        )
        if kw_cond:
            conditions_j.append(kw_cond)

    where_j = "WHERE " + " AND ".join(conditions_j) if conditions_j else ""
    sql_jalur = f"""
        SELECT nama_jalur, deskripsi, berlaku_untuk, syarat_utama,
               dokumen_wajib, biaya_daftar, catatan_khusus, website
        FROM jalur_pendaftaran
        {where_j}
        ORDER BY id
        LIMIT 6
    """.strip()

    conditions_jd: list[str] = []
    params_jd: list = []

    if nama_jalur:
        params_jd.append(f"%{nama_jalur}%")
        conditions_jd.append(f"LOWER(nama_jalur) ILIKE LOWER(${len(params_jd)})")

    where_jd = "WHERE " + " AND ".join(conditions_jd) if conditions_jd else ""
    sql_jadwal = f"""
        SELECT nama_jalur, gelombang, tanggal_buka, tanggal_tutup,
               tanggal_ujian, tanggal_pengumuman, keterangan
        FROM jadwal_pendaftaran
        {where_jd}
        ORDER BY nama_jalur, gelombang
        LIMIT 10
    """.strip()

    params_pmb: list = ["pmb"]
    extra_pmb = ""

    if keyword:
        kw_cond = _build_keyword_filter(
            keyword, params_pmb, ["kata_kunci", "pertanyaan", "jawaban"]
        )
        if kw_cond:
            extra_pmb = f" AND {kw_cond}"

    sql_pmb = f"""
        SELECT kategori, pertanyaan, jawaban
        FROM pengetahuan
        WHERE unit_kerja = $1
          {extra_pmb}
        ORDER BY kategori, id
        LIMIT 5
    """.strip()

    async with pool.acquire() as conn:
        rows_j  = await conn.fetch(sql_jalur,  *params_j)
        rows_jd = await conn.fetch(sql_jadwal, *params_jd)
        rows_pmb = await conn.fetch(sql_pmb,   *params_pmb)

    data = [dict(r) for r in rows_j] + \
           [dict(r) for r in rows_jd] + \
           [dict(r) for r in rows_pmb]

    return {
        "sql_query": (
            f"-- jalur_pendaftaran:\n{sql_jalur}\n\n"
            f"-- jadwal_pendaftaran:\n{sql_jadwal}\n\n"
            f"-- pengetahuan pmb:\n{sql_pmb}"
        ),
        "data": data
    }


async def _cari_biaya_kuliah(entities: dict) -> dict:
    """
    Mengambil data biaya kuliah dari tabel biaya_kuliah.
    Filter: nama_prodi, jenjang (S1/S2/Profesi), keyword.
    """
    pool = await get_pool()

    nama_prodi = entities.get("nama_prodi")
    jenjang    = entities.get("jenjang")
    keyword    = entities.get("keyword") or entities.get("topik")

    conditions: list[str] = []
    params: list = []

    if nama_prodi:
        params.append(f"%{nama_prodi}%")
        conditions.append(f"LOWER(nama_prodi) ILIKE LOWER(${len(params)})")

    if jenjang:
        params.append(f"%{jenjang}%")
        conditions.append(f"LOWER(jenjang) ILIKE LOWER(${len(params)})")

    _GENERIC_BIAYA_TERMS = {
        "dpfp", "dffp", "ice", "spp", "biaya kuliah", "biaya per sks",
        "spp tetap", "spp variabel", "biaya koas", "asrama teologi",
    }
    if keyword and keyword.strip().lower() in _GENERIC_BIAYA_TERMS:
        keyword = None

    if keyword and not nama_prodi:
        kw_cond = _build_keyword_filter(keyword, params, ["nama_prodi", "catatan"])
        if kw_cond:
            conditions.append(kw_cond)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
        SELECT nama_prodi, jenjang, dpfp, ice_per_level, spp_tetap_per_semester,
               spp_variabel_per_sks, tahun_akademik, catatan
        FROM biaya_kuliah
        {where}
        ORDER BY jenjang, nama_prodi
        LIMIT 15
    """.strip()

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return {"sql_query": sql, "data": [dict(r) for r in rows]}


async def _cari_program_studi(entities: dict) -> dict:
    """
    Mengambil data program studi dari tabel program_studi.
    Filter: nama_prodi, jenjang (S1/S2), fakultas, keyword.
    """
    pool = await get_pool()

    nama_prodi = entities.get("nama_prodi")
    jenjang    = entities.get("jenjang")
    fakultas   = entities.get("fakultas")
    keyword    = entities.get("keyword") or entities.get("topik")

    conditions: list[str] = []
    params: list = []

    if nama_prodi:
        params.append(f"%{nama_prodi}%")
        conditions.append(f"LOWER(nama_prodi) ILIKE LOWER(${len(params)})")

    if jenjang:
        params.append(f"%{jenjang}%")
        conditions.append(f"LOWER(jenjang) ILIKE LOWER(${len(params)})")

    if fakultas:
        params.append(f"%{fakultas}%")
        conditions.append(f"LOWER(fakultas) ILIKE LOWER(${len(params)})")

    if keyword and not nama_prodi:
        kw_cond = _build_keyword_filter(
            keyword, params, ["nama_prodi", "deskripsi", "fakultas"]
        )
        if kw_cond:
            conditions.append(kw_cond)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
        SELECT nama_prodi, jenjang, fakultas, akreditasi, deskripsi
        FROM program_studi
        {where}
        ORDER BY jenjang DESC, nama_prodi
        LIMIT 15
    """.strip()

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return {"sql_query": sql, "data": [dict(r) for r in rows]}


async def _cari_beasiswa(entities: dict) -> dict:
    """
    Menggabungkan data dari dua sumber:
    1. Tabel beasiswa — data lengkap program beasiswa UKDW
    2. Tabel pengetahuan biro_3 — Q&A prosedural beasiswa

    Filter: nama_beasiswa, jenis_beasiswa (mahasiswa_baru/mahasiswa_aktif/
            eksternal/pemerintah), keyword.
    """
    pool = await get_pool()

    nama_beasiswa  = entities.get("nama_beasiswa")
    jenis_beasiswa = entities.get("jenis_beasiswa")
    keyword        = entities.get("keyword") or entities.get("topik")

    conditions: list[str] = ["aktif = TRUE"]
    params: list = []

    if nama_beasiswa:
        params.append(f"%{nama_beasiswa}%")
        conditions.append(f"LOWER(nama_beasiswa) ILIKE LOWER(${len(params)})")

    if jenis_beasiswa:
        params.append(f"%{jenis_beasiswa}%")
        conditions.append(
            f"(LOWER(kategori) ILIKE LOWER(${len(params)}) OR "
            f" LOWER(jenis)    ILIKE LOWER(${len(params)}))"
        )

    if keyword and not nama_beasiswa:
        kw_cond = _build_keyword_filter(
            keyword, params,
            ["nama_beasiswa", "penyelenggara", "cakupan", "persyaratan"]
        )
        if kw_cond:
            conditions.append(kw_cond)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql_beasiswa = f"""
        SELECT nama_beasiswa, penyelenggara, kategori, jenis, sasaran,
               cakupan, persyaratan, cara_daftar, kontak, catatan
        FROM beasiswa
        {where}
        ORDER BY kategori, nama_beasiswa
        LIMIT 8
    """.strip()

    params_b3: list = ["biro_3", "%beasiswa%"]
    extra_b3 = ""

    if keyword:
        kw_cond = _build_keyword_filter(
            keyword, params_b3, ["kata_kunci", "pertanyaan", "jawaban"]
        )
        if kw_cond:
            extra_b3 = f" AND {kw_cond}"

    sql_b3 = f"""
        SELECT kategori, pertanyaan, jawaban
        FROM pengetahuan
        WHERE unit_kerja = $1
          AND LOWER(kategori) ILIKE $2
          {extra_b3}
        ORDER BY id
        LIMIT 4
    """.strip()

    async with pool.acquire() as conn:
        rows_beasiswa = await conn.fetch(sql_beasiswa, *params)
        rows_b3       = await conn.fetch(sql_b3,       *params_b3)

    data = [dict(r) for r in rows_beasiswa] + [dict(r) for r in rows_b3]
    return {
        "sql_query": f"-- beasiswa:\n{sql_beasiswa}\n\n-- pengetahuan biro_3:\n{sql_b3}",
        "data": data
    }


async def _cari_umum(entities: dict) -> dict:
    """
    Fallback handler untuk intent 'general' atau intent tidak dikenali.
    Mencari di seluruh tabel pengetahuan tanpa filter unit_kerja.

    Perbaikan:
      - Keyword selalu diutamakan sebagai filter utama.
      - Kategori entity digabung ke keyword (bukan filter kolom) — mencegah
        query tanpa hasil akibat nilai kategori yang tidak cocok dengan DB.
      - Jika benar-benar tidak ada keyword/kategori (pertanyaan off-topic murni),
        tidak melakukan query (biarkan response layer menangani sebagai off-topic).
    """
    pool = await get_pool()

    keyword  = entities.get("keyword") or entities.get("topik") or ""
    kategori = entities.get("kategori") or ""

    combined_kw = " ".join(filter(None, [keyword, kategori])).strip()

    if not combined_kw:
        logger.info("[SQL] _cari_umum: tidak ada keyword, skip query.")
        return {"sql_query": "", "data": []}

    where_clauses: list[str] = []
    params: list = []

    kw_cond = _build_keyword_filter(
        combined_kw, params, ["kata_kunci", "pertanyaan", "jawaban", "kategori"]
    )
    if kw_cond:
        where_clauses.append(kw_cond)

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    sql = f"""
        SELECT unit_kerja, kategori, pertanyaan, jawaban
        FROM pengetahuan
        {where}
        ORDER BY unit_kerja, kategori, id
        LIMIT 6
    """.strip()

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return {"sql_query": sql, "data": [dict(r) for r in rows]}