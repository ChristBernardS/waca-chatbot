"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     WACA — UKDW's Personal Chatbot System                    ║
║                           orchestration.py                                   ║
║              Stage 1 — M1 Orchestration (Intent & Entity Extraction)         ║
╚══════════════════════════════════════════════════════════════════════════════╝

INTENT YANG DIDUKUNG (diperbarui sesuai data real UKDW):

  Intent               Cakupan
  ──────────────────   ────────────────────────────────────────────────────────
  layanan_akademik     Transkip nilai, cuti, KTM, presensi, KRS, surat
                       keterangan, wisuda, yudisium, ijazah (Biro 1)
  kemahasiswaan        Alumni, asuransi kecelakaan, jas almamater, toga,
                       organisasi, PKM, program wajib, tracer study,
                       job fair, karir (Biro 3)
  kerjasama            MoU, MoA, kerjasama perusahaan, iklan, baliho,
                       videotron, relasi publik (Biro 4)
  student_exchange     Program pertukaran pelajar, IISMA, universitas
                       mitra luar negeri, OIA (Biro 4)
  pendaftaran          Jalur seleksi PMB, syarat, jadwal, proses
                       pendaftaran, pendaftaran ulang (PMB)
  biaya_kuliah         DPFP, Introduction to College English (ICE),
                       SPP Tetap, SPP Variabel, biaya koas,
                       asrama teologi (pmb.ukdw.ac.id)
  program_studi        Prodi, jurusan, fakultas, akreditasi, jenjang
  beasiswa             Jenis beasiswa, KIP, Djarum, GKI, GKJ, pinjaman
                       registrasi, cara daftar
  general              Semua yang tidak masuk kategori di atas
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


ORCHESTRATION_SYSTEM_PROMPT = """Ekstrak intent dan entitas dari pertanyaan pengguna WACA (chatbot FAQ UKDW).
Output HANYA JSON valid. JANGAN jawab pertanyaan. JANGAN tambah teks lain.

INTENT (pilih satu):
layanan_akademik  | transkrip nilai, KRS, registrasi matakuliah, cuti akademik, KTM, presensi, absensi, surat keterangan, wisuda, yudisium, ijazah, ruang kelas, SSAT, pddikti
kemahasiswaan     | alumni, asuransi, jas almamater, toga, BEM, PKM, lomba, OKA/P3DM/PKLM, job fair, pinjaman registrasi, karir, tracer study
kerjasama         | kerjasama perusahaan/instansi, MoU, MoA, PKS, IA, iklan, baliho, videotron, magang dari perusahaan
student_exchange  | pertukaran pelajar, exchange, IISMA, short-term, OIA, universitas mitra luar negeri, outbound (internal ke luar), inbound (mahasiswa asing ke UKDW)
pendaftaran       | PMB, jalur seleksi, syarat masuk, jadwal, daftar ulang, biaya pendaftaran, daftar, mendaftar
biaya_kuliah      | DPFP, Introduction to College English (ICE), SPP tetap/variabel, biaya per SKS, koas, asrama teologi, biaya kuliah, biaya semester
program_studi     | prodi, program studi, jurusan, fakultas, akreditasi, deskripsi, jenjang kuliah
beasiswa          | beasiswa, KIP-Kuliah, cara daftar beasiswa, status beasiswa
general           | salam, pertanyaan non-UKDW, atau topik yang BENAR-BENAR tidak masuk kategori manapun di atas

ATURAN INTENT:
- Jika pertanyaan menyebut KRS, transkrip, registrasi matakuliah, presensi, surat keterangan, cuti → WAJIB layanan_akademik (meskipun institusi bukan UKDW).
- Jika pertanyaan menyebut pendaftaran, daftar, pmb, jalur seleksi → WAJIB pendaftaran (meskipun universitas lain).
- Jika pertanyaan menyebut program studi, prodi, jurusan, fakultas → WAJIB program_studi (meskipun jenjang tidak tersedia).
- Jika pertanyaan menyebut pertukaran, exchange, student exchange → WAJIB student_exchange (meskipun untuk dosen atau kategori lain).
- Jika pertanyaan menyebut job fair, asuransi, alumni, kemahasiswaan → WAJIB kemahasiswaan (meskipun universitas lain).
- general HANYA untuk salam (halo, hi) atau topik BENAR-BENAR non-akademik UKDW (cuaca, berita, tokoh publik) yang TIDAK ada kata kunci apapun di atas.

ENTITAS (isi hanya yang relevan):
- keyword     : topik utama pertanyaan dalam 1-3 kata (mis. "registrasi matakuliah", "transkrip nilai", "cuti akademik", "surat keterangan aktif").
                WAJIB diisi untuk intent layanan_akademik, kemahasiswaan, kerjasama kecuali sudah ada entitas spesifik lain.
                Gunakan frasa yang MUNGKIN muncul di pertanyaan/jawaban/kata_kunci di database.
- kategori    : HANYA untuk dua kasus spesifik:
                (1) student_exchange: "outbound" atau "inbound"
                (2) beasiswa: "mahasiswa_baru" atau "mahasiswa_aktif" atau "eksternal"
                JANGAN isi kategori untuk intent layanan_akademik atau kemahasiswaan — gunakan keyword.
- nama_prodi  : EKSAK — "Filsafat Keilahian"|"Arsitektur"|"Desain Produk"|"Manajemen"|"Akuntansi"|"Biologi"|"Informatika"|"Sistem Informasi"|"Pendidikan Bahasa Inggris"|"Studi Humanitas"|"Kedokteran"|"Magister Manajemen"|"Magister Arsitektur"
- jenjang     : "S1"|"S2"|"Profesi"
- fakultas    : "Teologi"|"Arsitektur dan Desain"|"Bisnis"|"Bioteknologi"|"Teknologi Informasi"|"Ilmu Sosial dan Ilmu Politik"|"Kedokteran"
- jalur       : EKSAK — "Seleksi Prestasi"|"Seleksi Mandiri"|"Seleksi SKL"|"Seleksi UTBK"|"Seleksi Kedokteran"|"Seleksi Filsafat Keilahian"
- negara      : "Taiwan"|"Korea Selatan"|"Amerika Serikat"|"Jepang"|"Filipina"|"Jerman"|"India"
- universitas_mitra : "I-Shou University"|"Tunghai University"|"Handong Global University"|"KMOU"|"Goshen College"|"Ouachita Baptist University"|"Philippine Normal University"|"EvH Bochum"|"Hanseo University"|"Chang Jung Christian University"
- nama_beasiswa : EKSAK — "UKDW Scholarship"|"Beasiswa Talenta Duta Wacana"|"Beasiswa Samapta"|"Beasiswa Afirmasi Pendidikan Tinggi (ADiK)"|"KIP-Kuliah Merdeka"|"Beasiswa Kebutuhan"|"Beasiswa Bank BPD DIY"|"Beasiswa Prestasi Akademik Mahasiswa"|"Beasiswa Prestasi Umum (Seni/Olahraga/Softskill)"|"Beasiswa Poin SAC Tertinggi"|"Beasiswa Anak Karyawan/Pensiunan UKDW"|"Beasiswa ADARO"|"Beasiswa Scranton"|"Beasiswa Djarum Foundation"|"Beasiswa GKI Pondok Indah & GKI Kebayoran Baru"|"Beasiswa Sinode GKJW, Sinode GKJ, Sinode GKP"
- jenis_beasiswa : "mahasiswa_baru"|"mahasiswa_aktif"|"eksternal"|"pemerintah"

FORMAT: {"intent":"<intent>","entities":{<kunci>:<nilai>},"confidence":<0.0-1.0>}

CONTOH:
Input: "Bagaimana cara registrasi matakuliah / mengisi KRS?"
Output: {"intent":"layanan_akademik","entities":{"keyword":"registrasi matakuliah KRS"},"confidence":0.97}

Input: "Bagaimana cara melihat transkrip nilai?"
Output: {"intent":"layanan_akademik","entities":{"keyword":"transkrip nilai"},"confidence":0.97}

Input: "Cara cetak transkrip sementara di SSAT?"
Output: {"intent":"layanan_akademik","entities":{"keyword":"transkrip SSAT"},"confidence":0.97}

Input: "Bagaimana prosedur pengajuan cuti akademik?"
Output: {"intent":"layanan_akademik","entities":{"keyword":"cuti akademik prosedur"},"confidence":0.96}

Input: "Cara mengurus surat keterangan aktif kuliah?"
Output: {"intent":"layanan_akademik","entities":{"keyword":"surat keterangan aktif"},"confidence":0.97}

Input: "Bagaimana cara presensi / absensi kuliah?"
Output: {"intent":"layanan_akademik","entities":{"keyword":"presensi absensi"},"confidence":0.96}

Input: "Saya dari perusahaan X ingin menjalin kerjasama dengan UKDW, bagaimana prosedurnya?"
Output: {"intent":"kerjasama","entities":{"keyword":"prosedur kerjasama perusahaan"},"confidence":0.97}

Input: "Berapa SPP Informatika per semester?"
Output: {"intent":"biaya_kuliah","entities":{"nama_prodi":"Informatika","jenjang":"S1"},"confidence":0.96}

Input: "Kapan pendaftaran jalur Prestasi dibuka?"
Output: {"intent":"pendaftaran","entities":{"jalur":"Seleksi Prestasi"},"confidence":0.96}

Input: "Cara klaim asuransi kecelakaan mahasiswa?"
Output: {"intent":"kemahasiswaan","entities":{"keyword":"asuransi klaim"},"confidence":0.97}

Input: "Ada beasiswa untuk mahasiswa baru tidak mampu?"
Output: {"intent":"beasiswa","entities":{"nama_beasiswa":"Beasiswa Talenta Duta Wacana","jenis_beasiswa":"mahasiswa_baru"},"confidence":0.95}

Input: "Bagaimana daftar program pertukaran pelajar ke Korea Selatan?"
Output: {"intent":"student_exchange","entities":{"negara":"Korea Selatan","keyword":"pendaftaran"},"confidence":0.96}

Input: "Ada program exchange ke Jepang tidak?"
Output: {"intent":"student_exchange","entities":{"negara":"Jepang"},"confidence":0.97}

Input: "Prodi apa saja di Fakultas Teknologi Informasi?"
Output: {"intent":"program_studi","entities":{"fakultas":"Teknologi Informasi"},"confidence":0.95}

Input: "Bagaimana cara mahasiswa asing mendaftar untuk belajar di UKDW?"
Output: {"intent":"student_exchange","entities":{"kategori":"inbound","keyword":"pendaftaran"},"confidence":0.96}

Input: "Halo, apa yang bisa kamu bantu?"
Output: {"intent":"general","entities":{},"confidence":0.99}

Input: "Siapa itu Jokowi?"
Output: {"intent":"general","entities":{},"confidence":0.99}
"""

def _build_user_prompt(user_message: str, history: list) -> str:
    history_text = ""
    if history:
        recent = history[-4:]
        history_text = "\nKonteks percakapan sebelumnya:\n"
        for turn in recent:
            role    = turn.get("role", "user")
            content = turn.get("content", "")
            history_text += f"  {role}: {content}\n"
        history_text += "\n"
    return f"{history_text}Pertanyaan pengguna: {user_message}"


async def extract_intent_and_entities(user_message: str, history: list = []) -> dict:
    """
    Entry point Stage 1. Kirim pesan ke Ollama, ekstrak intent + entitas.
    Fallback ke keyword matching jika Ollama tidak tersedia.
    """
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model    = os.getenv("OLLAMA_MODEL",    "llama3.2")
    api_key  = os.getenv("OLLAMA_API_KEY",  "")
    req_hdrs = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    prompt = _build_user_prompt(user_message, history)

    payload = {
        "model":    model,
        "messages": [
            {"role": "system", "content": ORCHESTRATION_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt}
        ],
        "stream": False,
        "options": {
            "temperature":  0.1,
            "top_p":        0.9,
            "num_predict":  256
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=req_hdrs) as client:
            response = await client.post(f"{base_url}/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()

        raw_text = result["message"]["content"].strip()
        logger.debug(f"[M1] Output Ollama: {raw_text[:300]}")

        parsed     = _extract_json(raw_text)
        intent     = parsed.get("intent", "general")
        entities   = parsed.get("entities", {})
        confidence = parsed.get("confidence", 0.5)

        known_intents = {
            "layanan_akademik", "kemahasiswaan", "kerjasama", "student_exchange",
            "pendaftaran", "biaya_kuliah", "program_studi", "beasiswa", "general"
        }
        if intent not in known_intents:
            logger.warning(f"[M1] Intent tidak dikenal: '{intent}' → default 'general'")
            intent = "general"

        llm_result = {"intent": intent, "entities": entities, "confidence": confidence}
        return _apply_intent_override(llm_result, user_message)

    except httpx.ConnectError:
        logger.warning(f"[M1] Tidak dapat terhubung ke Ollama. Gunakan fallback keyword.")
        return _keyword_fallback(user_message)
    except httpx.TimeoutException:
        logger.warning("[M1] Ollama timeout. Gunakan fallback keyword.")
        return _keyword_fallback(user_message)
    except Exception as e:
        logger.error(f"[M1] Error tidak terduga: {e}. Gunakan fallback keyword.")
        return _keyword_fallback(user_message)

async def debug_m1_raw(user_message: str, history: list = []) -> dict:
    """
    Versi debug dari extract_intent_and_entities.
    Mengembalikan RAW output Ollama (sebelum di-parse) sekaligus
    hasil setiap tahap pemrosesan, sehingga bisa dilihat persis
    apa yang LLM hasilkan di M1.
    """
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model    = os.getenv("OLLAMA_MODEL",    "llama3.2")
    api_key  = os.getenv("OLLAMA_API_KEY",  "")
    req_hdrs = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    prompt = _build_user_prompt(user_message, history)

    payload = {
        "model":    model,
        "messages": [
            {"role": "system", "content": ORCHESTRATION_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt}
        ],
        "stream": False,
        "think":  False,
        "options": {
            "temperature": 0.1,
            "top_p":       0.9,
            "num_predict": 256
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=req_hdrs) as client:
            response = await client.post(f"{base_url}/api/chat", json=payload)
            response.raise_for_status()
            ollama_response = response.json()

        raw_text = ollama_response["message"]["content"].strip()

        step1 = re.sub(r"<think>.*</think>", "", raw_text, flags=re.DOTALL).strip()
        step2 = re.sub(r"```(?:json)?", "", step1).strip().strip("`").strip()

        parse_success = False
        parse_error   = None
        parsed_result = {}
        try:
            parsed_result = json.loads(step2)
            parse_success = True
        except json.JSONDecodeError as e:
            parse_error = str(e)

        final_result = {}
        if parse_success:
            final_result = _apply_intent_override(parsed_result, user_message)

        return {
            "raw_llm_output": raw_text,

            "ollama_metadata": {
                "model":              ollama_response.get("model"),
                "done":               ollama_response.get("done"),
                "total_duration_ms":  round(ollama_response.get("total_duration", 0) / 1e6, 2),
                "prompt_eval_count":  ollama_response.get("prompt_eval_count"),
                "eval_count":         ollama_response.get("eval_count"),
            },

            "processing_steps": {
                "after_remove_think_block": step1,
                "after_remove_markdown_fence": step2,
                "json_parse_success": parse_success,
                "json_parse_error":   parse_error,
            },

            "parsed_result":  parsed_result,
            "final_result":   final_result,
            "source": "ollama_llm",
        }

    except httpx.ConnectError:
        return {"error": "Tidak dapat terhubung ke Ollama", "source": "connection_error"}
    except httpx.TimeoutException:
        return {"error": "Ollama timeout", "source": "timeout_error"}
    except Exception as e:
        return {"error": str(e), "source": "unexpected_error"}

def _extract_json(text: str) -> dict:
    """Ekstrak JSON dari output LLM, tangani think blocks, markdown fences, dan teks berlebih."""
    text = re.sub(r"<think>.*</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    logger.warning(f"[M1] Tidak bisa parse JSON dari: {text[:200]}")
    return {"intent": "general", "entities": {}, "confidence": 0.3}


_DAFTAR_PRODI = [
    "Pendidikan Bahasa Inggris",
    "Filsafat Keilahian",
    "Desain Produk",
    "Sistem Informasi",
    "Studi Humanitas",
    "Arsitektur",
    "Manajemen",
    "Akuntansi",
    "Biologi",
    "Informatika",
    "Kedokteran",
]


def _keyword_fallback(message: str) -> dict:
    """
    Deteksi intent berbasis kata kunci sebagai fallback saat Ollama offline.
    Urutan pengecekan dari yang paling spesifik ke yang paling umum.
    """
    msg = message.lower()

    if any(w in msg for w in ["exchange", "pertukaran pelajar", "iisma", "oia", "luar negeri", "goshen", "ouachita"]):
        kw = next((w for w in ["exchange", "pertukaran pelajar", "iisma", "oia"] if w in msg), None)
        return {"intent": "student_exchange", "entities": {"keyword": kw} if kw else {}, "confidence": 0.7}

    if any(w in msg for w in ["dpfp", "dffp", "ice", "spp", "biaya kuliah", "biaya per sks", "spp tetap", "spp variabel", "biaya koas", "asrama teologi"]):
        entities: dict = {}
        for prodi in _DAFTAR_PRODI:
            if prodi.lower() in msg:
                entities["nama_prodi"] = prodi
                break
        if "s2" in msg or "magister" in msg or "pascasarjana" in msg:
            entities["jenjang"] = "S2"
        elif "profesi" in msg or "koas" in msg:
            entities["jenjang"] = "Profesi"
        elif "s1" in msg:
            entities["jenjang"] = "S1"
        if "nama_prodi" not in entities:
            kw = next((w for w in ["dpfp", "dffp", "spp", "biaya kuliah", "biaya per sks"] if w in msg), None)
            if kw:
                entities["keyword"] = kw
        return {"intent": "biaya_kuliah", "entities": entities, "confidence": 0.75}

    if any(w in msg for w in ["beasiswa", "kip-kuliah", "kip kuliah", "djarum", "gki", "gkj", "pinjaman registrasi", "beasiswa prestasi"]):
        kw = next((w for w in ["beasiswa", "kip-kuliah", "beasiswa prestasi"] if w in msg), None)
        return {"intent": "beasiswa", "entities": {"keyword": kw} if kw else {}, "confidence": 0.75}

    if any(w in msg for w in ["daftar", "pendaftaran", "pmb", "seleksi prestasi", "seleksi mandiri", "jalur skl", "utbk", "seleksi kedokteran", "penerimaan mahasiswa"]):
        kw = next((w for w in ["pendaftaran", "pmb", "seleksi prestasi", "seleksi mandiri", "utbk"] if w in msg), None)
        return {"intent": "pendaftaran", "entities": {"keyword": kw} if kw else {}, "confidence": 0.72}

    if any(w in msg for w in ["prodi", "program studi", "jurusan", "fakultas", "akreditasi", "informatika", "kedokteran", "arsitektur", "manajemen", "akuntansi"]):
        kw = next((w for w in ["prodi", "program studi", "jurusan", "akreditasi"] if w in msg), None)
        return {"intent": "program_studi", "entities": {"keyword": kw} if kw else {}, "confidence": 0.72}

    _layanan_map = {
        "transkrip": "transkrip nilai",
        "nilai": "transkrip nilai",
        "krs": "registrasi matakuliah KRS",
        "registrasi matakuliah": "registrasi matakuliah KRS",
        "isi krs": "registrasi matakuliah KRS",
        "cuti": "cuti akademik",
        "ktm": "kartu tanda mahasiswa KTM",
        "kartu mahasiswa": "kartu tanda mahasiswa KTM",
        "presensi": "presensi absensi",
        "absen": "presensi absensi",
        "surat keterangan": "surat keterangan",
        "wisuda": "wisuda",
        "yudisium": "yudisium",
        "ijazah": "ijazah",
        "ssat": "SSAT portal akademik",
        "pddikti": "pddikti",
        "ruang kelas": "ruang kelas",
    }
    for trigger, keyword_val in _layanan_map.items():
        if trigger in msg:
            return {"intent": "layanan_akademik", "entities": {"keyword": keyword_val}, "confidence": 0.73}

    _kemahasiswaan_map = {
        "alumni": "alumni", "asuransi": "asuransi", "jas almamater": "jas almamater",
        "toga": "toga wisuda", "bem": "BEM organisasi", "pkm": "PKM lomba",
        "oka": "OKA program wajib", "p3dm": "P3DM program wajib",
        "pklm": "PKLM program wajib", "tracer study": "tracer study",
        "job fair": "job fair karir", "karir": "karir lowongan",
        "pinjaman": "pinjaman registrasi", "sac": "SAC poin", "lomba": "lomba kompetisi",
    }
    for trigger, keyword_val in _kemahasiswaan_map.items():
        if trigger in msg:
            return {"intent": "kemahasiswaan", "entities": {"keyword": keyword_val}, "confidence": 0.72}

    if any(w in msg for w in ["kerjasama", "mou", "moa", "pks", "iklan", "baliho", "videotron", "magang perusahaan", "sponsor", "relasi publik"]):
        kw = next((w for w in ["kerjasama", "mou", "moa", "iklan", "baliho"] if w in msg), None)
        return {"intent": "kerjasama", "entities": {"keyword": kw} if kw else {}, "confidence": 0.72}

    return {"intent": "general", "entities": {}, "confidence": 0.5}



_INTENT_OVERRIDE_RULES: list[tuple[list[str], str, dict]] = [
    (["transkrip", "lihat nilai", "cetak transkrip"],
     "layanan_akademik", {"keyword": "transkrip nilai"}),
    (["registrasi matakuliah", "isi krs", "input krs", "pengisian krs"],
     "layanan_akademik", {"keyword": "registrasi matakuliah KRS"}),
    (["cuti akademik", "pengajuan cuti", "ambil cuti"],
     "layanan_akademik", {"keyword": "cuti akademik"}),
    (["surat keterangan aktif", "surat keterangan mahasiswa", "surat aktif kuliah"],
     "layanan_akademik", {"keyword": "surat keterangan aktif"}),
    (["presensi", "absensi", "kehadiran kuliah"],
     "layanan_akademik", {"keyword": "presensi absensi"}),
    (["kartu tanda mahasiswa", "cetak ktm", "ktm hilang"],
     "layanan_akademik", {"keyword": "kartu tanda mahasiswa KTM"}),
]


def _apply_intent_override(result: dict, user_message: str) -> dict:
    """
    Koreksi post-LLM: jika hasil LLM tidak sesuai dengan kata kunci spesifik
    di pesan pengguna, terapkan override berdasarkan _INTENT_OVERRIDE_RULES.

    Kasus target:
      - LLM mengembalikan intent=general untuk pertanyaan yang jelas layanan_akademik
      - LLM mengembalikan intent=layanan_akademik tapi entities kosong (keyword hilang)

    Override hanya diterapkan jika:
      (1) Intent LLM adalah 'general' tapi trigger rule cocok, ATAU
      (2) Intent benar tapi entities.keyword kosong dan trigger rule cocok
    """
    msg_lower = user_message.lower()
    llm_intent = result.get("intent", "general")
    llm_entities = result.get("entities", {})

    for triggers, correct_intent, base_entities in _INTENT_OVERRIDE_RULES:
        if any(t in msg_lower for t in triggers):
            if llm_intent != correct_intent:
                logger.info(
                    f"[M1] Override intent: '{llm_intent}' → '{correct_intent}' "
                    f"(trigger: {[t for t in triggers if t in msg_lower]})"
                )
                merged_entities = {**base_entities, **llm_entities}
                if "keyword" not in llm_entities or not llm_entities["keyword"]:
                    merged_entities["keyword"] = base_entities.get("keyword", "")
                return {**result, "intent": correct_intent, "entities": merged_entities}

            if correct_intent == llm_intent and not llm_entities.get("keyword"):
                logger.info(
                    f"[M1] Override entities: menambahkan keyword='{base_entities['keyword']}' "
                    f"untuk intent '{llm_intent}'"
                )
                return {**result, "entities": {**base_entities, **llm_entities}}

    return result