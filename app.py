import os
import streamlit as st
from groq import Groq
from SPARQLWrapper import SPARQLWrapper, JSON as SPARQL_JSON
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# KONFIGURASI
# ─────────────────────────────────────────────
FUSEKI_ENDPOINT = "http://localhost:3030/dataset_hadits/query"
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
PREFIX = """
PREFIX ex:  <http://proyekakhir.ac.id/concept/>
PREFIX rel: <http://proyekakhir.ac.id/relation/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
"""

# ─────────────────────────────────────────────
# HELPER SPARQL
# ─────────────────────────────────────────────
def run_sparql(query: str):
    sparql = SPARQLWrapper(FUSEKI_ENDPOINT)
    sparql.setQuery(query)
    sparql.setReturnFormat(SPARQL_JSON)
    try:
        results = sparql.query().convert()
        return results["results"]["bindings"]
    except Exception as e:
        st.error(f"❌ Gagal terhubung ke Fuseki: {e}")
        return []

def uri_to_label(uri: str) -> str:
    local = uri.split("/")[-1]
    return local.replace("_", " ")

# ─────────────────────────────────────────────
# QUERY FUNCTIONS
# ─────────────────────────────────────────────
def search_hadis(keyword: str):
    safe = keyword.replace('"', '\\"')
    query = PREFIX + f"""
SELECT DISTINCT ?hadis ?teksArab ?teksLatin ?terjemahan ?perawi ?topik WHERE {{
  ?hadis a ex:Hadis ;
         rel:teksArab    ?teksArab ;
         rel:teksLatin   ?teksLatin ;
         rel:terjemahan  ?terjemahan ;
         rel:diriwayatkanOleh ?perawiURI ;
         rel:membahasTopik    ?topikURI .
         
  # Ambil frasa yang nyambung ke hadis ini (Opsional/LEFT JOIN)
  OPTIONAL {{
    ?frasa rel:munculDalam ?hadis ;
           rel:teksLatin ?frasaLatin ;
           rel:terjemahan ?frasaArti .
  }}

  BIND(REPLACE(STR(?perawiURI), ".*/", "") AS ?perawi)
  BIND(REPLACE(STR(?topikURI),  ".*/", "") AS ?topik)
  
  # Cari di Hadis UTAMA ATAU di FRASA
  FILTER(
    CONTAINS(LCASE(STR(?teksArab)),   LCASE("{safe}")) ||
    CONTAINS(LCASE(STR(?teksLatin)),  LCASE("{safe}")) ||
    CONTAINS(LCASE(STR(?terjemahan)), LCASE("{safe}")) ||
    CONTAINS(LCASE(STR(?frasaLatin)), LCASE("{safe}")) ||
    CONTAINS(LCASE(STR(?frasaArti)),  LCASE("{safe}"))
  )
}}
ORDER BY ?hadis
"""
    return run_sparql(query)

def get_frasa_for_hadis(hadis_uri: str):
    query = PREFIX + f"""
SELECT ?frasa ?teksArab ?teksLatin ?terjemahan ?jumlahKarakter WHERE {{
  ?frasa a ex:Frasa ;
         rel:munculDalam  <{hadis_uri}> ;
         rel:teksArab     ?teksArab ;
         rel:teksLatin    ?teksLatin ;
         rel:terjemahan   ?terjemahan ;
         rel:jumlahKarakterArab ?jumlahKarakter .
}}
ORDER BY ?frasa
"""
    return run_sparql(query)

def search_by_perawi(perawi_label: str):
    safe = perawi_label.replace(" ", "_")
    query = PREFIX + f"""
SELECT DISTINCT ?hadis ?teksArab ?teksLatin ?terjemahan ?topik WHERE {{
  ?hadis a ex:Hadis ;
         rel:teksArab   ?teksArab ;
         rel:teksLatin  ?teksLatin ;
         rel:terjemahan ?terjemahan ;
         rel:diriwayatkanOleh ex:{safe} ;
         rel:membahasTopik    ?topikURI .
  BIND(REPLACE(STR(?topikURI), ".*/", "") AS ?topik)
}}
ORDER BY ?hadis
"""
    return run_sparql(query)

def search_by_topik(topik_label: str):
    safe = topik_label.replace(" ", "_")
    query = PREFIX + f"""
SELECT DISTINCT ?hadis ?teksArab ?teksLatin ?terjemahan ?perawi WHERE {{
  ?hadis a ex:Hadis ;
         rel:teksArab   ?teksArab ;
         rel:teksLatin  ?teksLatin ;
         rel:terjemahan ?terjemahan ;
         rel:membahasTopik    ex:{safe} ;
         rel:diriwayatkanOleh ?perawiURI .
  BIND(REPLACE(STR(?perawiURI), ".*/", "") AS ?perawi)
}}
ORDER BY ?hadis
"""
    return run_sparql(query)

def get_all_perawi():
    query = PREFIX + """
SELECT DISTINCT ?perawiURI WHERE { ?p a ex:Perawi . BIND(?p AS ?perawiURI) }
ORDER BY ?perawiURI
"""
    rows = run_sparql(query)
    return [uri_to_label(r["perawiURI"]["value"]) for r in rows]

def get_all_topik():
    query = PREFIX + """
SELECT DISTINCT ?topikURI WHERE {
  ?h a ex:Hadis ; rel:membahasTopik ?topikURI .
}
ORDER BY ?topikURI
"""
    rows = run_sparql(query)
    return [uri_to_label(r["topikURI"]["value"]) for r in rows]

def get_all_hadis():
    query = PREFIX + """
SELECT ?hadis ?teksArab ?teksLatin ?terjemahan ?perawi ?topik WHERE {
  ?hadis a ex:Hadis ;
         rel:teksArab   ?teksArab ;
         rel:teksLatin  ?teksLatin ;
         rel:terjemahan ?terjemahan ;
         rel:diriwayatkanOleh ?perawiURI ;
         rel:membahasTopik    ?topikURI .
  BIND(REPLACE(STR(?perawiURI), ".*/", "") AS ?perawi)
  BIND(REPLACE(STR(?topikURI),  ".*/", "") AS ?topik)
}
ORDER BY ?hadis
"""
    return run_sparql(query)

# ─────────────────────────────────────────────
# RAG + GROQ
# ─────────────────────────────────────────────
MAX_CONTEXT_CHARS = 8000

def build_context_from_results(hadis_rows, frasa_map: dict) -> str:
    lines = []
    for row in hadis_rows:
        uri    = row["hadis"]["value"]
        arab   = row["teksArab"]["value"]
        latin  = row["teksLatin"]["value"]
        terj   = row["terjemahan"]["value"]
        perawi = row.get("perawi", {}).get("value", "-").replace("_", " ")
        topik  = row.get("topik",  {}).get("value", "-").replace("_", " ")
        hadis_id = uri.split("/")[-1]

        lines.append(f"=== {hadis_id} ===")
        lines.append(f"Topik    : {topik}")
        lines.append(f"Perawi   : {perawi}")
        lines.append(f"Arab     : {arab}")
        lines.append(f"Latin    : {latin}")
        lines.append(f"Terjemah : {terj}")

        if uri in frasa_map:
            lines.append("Frasa-frasa:")
            for i, f in enumerate(frasa_map[uri], 1):
                lines.append(
                    f"  {i}. [{f['teksArab']['value']}] "
                    f"({f['teksLatin']['value']}) = {f['terjemahan']['value']}"
                )
        lines.append("")
    return "\n".join(lines)

def generate_syarah(keyword: str, context: str) -> str:
    if not GROQ_API_KEY:
        return "⚠️ API Key Groq belum dikonfigurasi. Tambahkan `GROQ_API_KEY` di file `.env`."

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n...[konteks dipotong agar tidak melebihi batas token]"

    prompt = f"""Kamu adalah seorang ulama dan pakar hadis yang bertugas menjelaskan Hadis Arbain Nawawi secara mendalam.

Berikut adalah data hadis yang ditemukan dari knowledge graph RDF berdasarkan pencarian kata kunci: "{keyword}"

=== DATA HADIS (dari Knowledge Graph) ===
{context}
=== AKHIR DATA ===

Berdasarkan HANYA data di atas (jangan menambahkan hadis lain yang tidak ada di data), berikan:
1. **Penjelasan Umum**: Apa tema utama yang menghubungkan hadis-hadis tersebut?
2. **Syarah Per-Hadis**: Jelaskan makna dan kandungan setiap hadis secara singkat dan jelas, termasuk analisis frasa-frasa pentingnya.
3. **Relevansi dengan Kata Kunci**: Mengapa hadis ini relevan dengan pencarian "{keyword}"?
4. **Hikmah & Penerapan**: Pelajaran dan aplikasi praktis dalam kehidupan sehari-hari.

Gunakan Bahasa Indonesia yang baik, lugas, dan mudah dipahami. Jawab berdasarkan data yang tersedia saja."""

    try:
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        st.session_state.groq_model_used = response.model
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ Gagal menghubungi Groq API: {e}"

# ─────────────────────────────────────────────
# RENDER HADIS CARD
# ─────────────────────────────────────────────
def render_hadis_card(row, idx: int, show_frasa: bool = True):
    uri    = row["hadis"]["value"]
    arab   = row["teksArab"]["value"]
    latin  = row["teksLatin"]["value"]
    terj   = row["terjemahan"]["value"]
    perawi = row.get("perawi", {}).get("value", "-").replace("_", " ")
    topik  = row.get("topik",  {}).get("value", "-").replace("_", " ")
    hadis_id = uri.split("/")[-1].replace("_", " ")

    with st.container():
        st.markdown(f"""
<div style="
  background: linear-gradient(135deg, #1a3a2a 0%, #0f2419 100%);
  border-left: 4px solid #4CAF50;
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 16px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
    <span style="background:#4CAF50; color:white; padding:4px 12px; border-radius:20px; font-size:13px; font-weight:600;">
      📖 {hadis_id}
    </span>
    <span style="color:#a8d5b5; font-size:12px;">🏷️ {topik}</span>
  </div>
  <p style="font-family:'Amiri', 'Traditional Arabic', serif; font-size:26px; color:#f5f0e8;
            text-align:right; direction:rtl; line-height:1.8; margin:12px 0;">
    {arab}
  </p>
  <p style="color:#a8d5b5; font-style:italic; font-size:14px; margin:8px 0;">
    {latin}
  </p>
  <p style="color:#e8f5e9; font-size:15px; margin:8px 0; line-height:1.6;">
    {terj}
  </p>
  <hr style="border:none; border-top:1px solid #2d5a3d; margin:12px 0;">
  <span style="color:#80cbc4; font-size:13px;">👤 Diriwayatkan oleh: <strong style="color:#b2dfdb;">{perawi}</strong></span>
</div>
""", unsafe_allow_html=True)

        if show_frasa:
            with st.expander(f"🔍 Lihat Analisis Frasa per Kata — {hadis_id}"):
                frasa_rows = get_frasa_for_hadis(uri)
                if frasa_rows:
                    st.markdown("#### Breakdown Frasa (Word-by-Word Analysis)")
                    cols_header = st.columns([0.8, 3, 3, 3, 1.5])
                    for h, t in zip(cols_header, ["No.", "Arab 🕌", "Latin", "Terjemahan 🇮🇩", "Karakter"]):
                        h.markdown(f"**{t}**")
                    st.markdown("---")
                    for i, f in enumerate(frasa_rows, 1):
                        cols = st.columns([0.8, 3, 3, 3, 1.5])
                        cols[0].markdown(f"**{i}**")
                        cols[1].markdown(
                            f"<p style='font-family:serif;font-size:18px;direction:rtl;text-align:right;'>{f['teksArab']['value']}</p>",
                            unsafe_allow_html=True
                        )
                        cols[2].markdown(f"*{f['teksLatin']['value']}*")
                        cols[3].markdown(f"{f['terjemahan']['value']}")
                        cols[4].markdown(f"`{f['jumlahKarakter']['value']}`")
                else:
                    st.info("Tidak ada data frasa untuk hadis ini.")

# ─────────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="Hadis Arbain — Pencarian Semantik",
        page_icon="🕌",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # ── CSS Global ──
    st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Amiri:ital@0;1&display=swap');
  .stApp { background-color: #0d1f12; }
                
  [data-testid="stHeader"] { background-color: transparent; }
  .block-container { padding-top: 2rem; }
                
  /* Input text field enhancements */
  .stTextInput > div > div > input {
    background-color: #1a2e1e; color: #ffffff;
    border: 1px solid #66bb6a; border-radius: 8px;
    padding: 12px 14px;
    font-size: 16px;
  }
  
  /* Placeholder visibility fix */
  .stTextInput > div > div > input::placeholder {
    color: #a5d6a7;
    opacity: 0.9;
  }
  
  /* Button enhancements */
  .stButton > button {
    background: linear-gradient(135deg, #2e7d32, #1b5e20);
    color: white; border: none; border-radius: 8px;
    padding: 8px 16px; font-weight: 600;
    transition: all 0.2s;
  }
  .stButton > button:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(76,175,80,0.5); }
  
  .stSelectbox > div > div { background-color: #1a2e1e; color: #e8f5e9; border: 1px solid #66bb6a; }
  .stSidebar { background-color: #0a1a0e; }
  .stSidebar .stMarkdown h2 { color: #4CAF50; }
  .stMetric { background-color: #1a2e1e; border-radius: 8px; padding: 12px; border: 1px solid #2d5a3d; }
  div[data-testid="stExpander"] { 
      background-color: #132118 !important; 
      border: 1px solid #2d5a3d !important; 
      border-radius: 8px !important; 
  }
  div[data-testid="stExpander"] details summary {
      background-color: #132118 !important;
      color: #a5d6a7 !important;
      border-radius: 8px !important;
  }
  div[data-testid="stExpander"] details summary:hover {
      background-color: #1a2e1e !important;
  }
  div[data-testid="stExpanderDetails"] {
      background-color: #132118 !important;
  }
  h1, h2, h3, h4, h5, h6 { color: #a5d6a7 !important; }
  p, li { color: #e8f5e9; }
  .stAlert { background-color: #1a2e1e; border: 1px solid #4CAF50; }
  hr { border-color: #2d5a3d !important; }
  
  /* Keyboard button style */
  div[data-testid="column"] .stButton > button {
    padding: 4px 8px;
    font-size: 18px;
  }
</style>
"""
, unsafe_allow_html=True)

    # ── HEADER ──
    st.markdown("""
<div style="text-align:center; padding: 24px 0 16px 0;">
  <h1 style="font-size:40px; color:#81c784 !important; margin:0;">🕌 Hadis Arbain Nawawi</h1>
  <p style="color:#a5d6a7; font-size:16px; margin:4px 0;">
    Sistem Pencarian Semantik Berbasis RDF & SPARQL
  </p>
  <p style="color:#66bb6a; font-size:13px;">
    Knowledge Graph · Ontologi OWL · Apache Jena Fuseki · AI Syarah (RAG + Groq LLaMA)
  </p>
</div>
<hr>
""", unsafe_allow_html=True)

    # ── SIDEBAR ──
    with st.sidebar:
        st.markdown("## ⚙️ Mode Pencarian")
        mode = st.radio(
            "Pilih mode:",
            ["🔎 Cari Kata Kunci", "👤 Filter Perawi", "🏷️ Filter Topik", "📚 Semua Hadis"],
            label_visibility="collapsed"
        )
        st.markdown("---")
        st.markdown("## 🤖 AI Syarah")
        enable_ai = st.toggle("Aktifkan AI Syarah", value=False)
        if enable_ai:
            st.info("AI akan menjelaskan hadis yang ditemukan menggunakan RAG + Groq (LLaMA 3.3 70B).")
        st.markdown("---")
        st.markdown("""
**📌 Tentang Sistem**
- Dataset: 20 Hadis Arbain Nawawi
- Format: RDF Turtle (.ttl)
- Triple Store: Apache Jena Fuseki
- Ontologi: OWL (Protégé)
- AI: Groq LLaMA 3.3 70B (RAG)
""")

    # ── INISIALISASI SESSION STATE ──
    if "search_results" not in st.session_state:
        st.session_state.search_results = []
    if "search_keyword" not in st.session_state:
        st.session_state.search_keyword = ""
    if "search_mode" not in st.session_state:
        st.session_state.search_mode = ""
    if "syarah_text" not in st.session_state:
        st.session_state.syarah_text = ""
    if "syarah_context" not in st.session_state:
        st.session_state.syarah_context = ""
    if "groq_model_used" not in st.session_state:
        st.session_state.groq_model_used = ""

    results = []
    keyword = ""

    # Callbacks for Keyboard Arab
    def add_char(c):
        if "kw_input" not in st.session_state:
            st.session_state.kw_input = ""
        st.session_state.kw_input += c

    def backspace_char():
        if "kw_input" in st.session_state and len(st.session_state.kw_input) > 0:
            st.session_state.kw_input = st.session_state.kw_input[:-1]

    def clear_chars():
        st.session_state.kw_input = ""

    # ────── MODE 1: Kata Kunci ──────
    if "Cari Kata Kunci" in mode:
        st.markdown("### 🔎 Pencarian Teks")
        st.markdown("Masukkan kata kunci dalam **bahasa Arab**, **Latin**, atau **Indonesia**:")

        col1, col2 = st.columns([5, 1])
        with col1:
            keyword_input = st.text_input(
                "Kata kunci",
                placeholder="Contoh: niat | اَلنِّيَّةُ | ikhlas | iman ...",
                label_visibility="collapsed",
                key="kw_input"
            )
        with col2:
            search_btn = st.button("Cari", use_container_width=True, key="btn_cari")

        # Arabic Keyboard
        show_keyboard = st.toggle("⌨️ Tampilkan Keyboard Arab", value=False)
        if show_keyboard:
            kb_container = st.container()
            with kb_container:
                st.markdown("<span id='keyboard-marker'></span>", unsafe_allow_html=True)
                st.markdown("""
                <style>
                /* Style the closest vertical block container */
                div[data-testid="stVerticalBlock"]:has(> div.element-container span#keyboard-marker) {
                    background-color: #132118;
                    padding: 16px;
                    border-radius: 8px;
                    border: 1px solid #2d5a3d;
                    margin-top: 8px;
                    margin-bottom: 16px;
                    display: flex;
                    flex-wrap: wrap;
                    justify-content: center;
                    gap: 6px;
                    flex-direction: row;
                }
                
                div[data-testid="stVerticalBlock"]:has(> div.element-container span#keyboard-marker) > div.element-container {
                    width: auto !important;
                }
                
                div.element-container:has(span#keyboard-marker) {
                    display: none;
                }
                
                div[data-testid="stVerticalBlock"]:has(> div.element-container span#keyboard-marker) .stButton > button {
                    padding: 8px 14px !important;
                    font-size: 20px !important;
                    min-width: 45px;
                }
                </style>
                """, unsafe_allow_html=True)
                
                chars = ['ض', 'ص', 'ث', 'ق', 'ف', 'غ', 'ع', 'ه', 'خ', 'ح', 'ج', 'د', 
                         'ش', 'س', 'ي', 'ب', 'ل', 'ا', 'ت', 'ن', 'م', 'ك', 'ط',
                         'ذ', 'ر', 'ز', 'و', 'ة', 'ى', 'لا', 'ؤ', 'ء', 'ئ']
                
                for c in chars:
                    st.button(c, on_click=add_char, args=(c,), key=f"kb_{c}")
                
                st.button("Spasi", on_click=add_char, args=(" ",), key="kb_space")
                st.button("⌫ Hapus", on_click=backspace_char, key="kb_backspace")
                st.button("🗑️ Kosongkan", on_click=clear_chars, key="kb_clear")

        if search_btn and keyword_input.strip():
            with st.spinner("Mencari di knowledge graph..."):
                st.session_state.search_results = search_hadis(keyword_input.strip())
                st.session_state.search_keyword = keyword_input.strip()
                st.session_state.search_mode = "keyword"
                st.session_state.syarah_text = ""
                st.session_state.groq_model_used = ""

        if st.session_state.search_mode == "keyword":
            keyword = st.session_state.search_keyword
            results = st.session_state.search_results

    # ────── MODE 2: Perawi ──────
    elif "Perawi" in mode:
        st.markdown("### 👤 Filter Berdasarkan Perawi")
        with st.spinner("Memuat daftar perawi..."):
            perawi_list = get_all_perawi()
        selected_perawi = st.selectbox("Pilih Perawi:", perawi_list, key="sel_perawi")
        if st.button("Tampilkan Hadis", key="btn_perawi"):
            with st.spinner("Mengambil data dari Fuseki..."):
                st.session_state.search_results = search_by_perawi(selected_perawi)
                st.session_state.search_keyword = selected_perawi
                st.session_state.search_mode = "perawi"
                st.session_state.syarah_text = ""
                st.session_state.groq_model_used = ""

        if st.session_state.search_mode == "perawi":
            keyword = st.session_state.search_keyword
            results = st.session_state.search_results

    # ────── MODE 3: Topik ──────
    elif "Topik" in mode:
        st.markdown("### 🏷️ Filter Berdasarkan Topik")
        with st.spinner("Memuat daftar topik..."):
            topik_list = get_all_topik()
        selected_topik = st.selectbox("Pilih Topik:", topik_list, key="sel_topik")
        if st.button("Tampilkan Hadis", key="btn_topik"):
            with st.spinner("Mengambil data dari Fuseki..."):
                st.session_state.search_results = search_by_topik(selected_topik)
                st.session_state.search_keyword = selected_topik.replace("_", " ")
                st.session_state.search_mode = "topik"
                st.session_state.syarah_text = ""
                st.session_state.groq_model_used = ""

        if st.session_state.search_mode == "topik":
            keyword = st.session_state.search_keyword
            results = st.session_state.search_results

    # ────── MODE 4: Semua Hadis ──────
    elif "Semua Hadis" in mode:
        st.markdown("### 📚 Seluruh Hadis Arbain Nawawi")
        if st.session_state.search_mode != "all":
            with st.spinner("Memuat semua hadis..."):
                st.session_state.search_results = get_all_hadis()
                st.session_state.search_keyword = "semua hadis"
                st.session_state.search_mode = "all"
        keyword = st.session_state.search_keyword
        results = st.session_state.search_results

    # ─────────────────────────────────────────
    # TAMPILKAN HASIL
    # ─────────────────────────────────────────
    if results:
        st.markdown("---")
        st.markdown(f"**Ditemukan {len(results)} hadis**")

        m1, m2, m3 = st.columns(3)
        m1.metric("📖 Hadis Ditemukan", len(results))
        perawi_set = {r.get("perawi", {}).get("value", "") for r in results}
        m2.metric("👤 Perawi Unik", len([p for p in perawi_set if p]))
        topik_set = {r.get("topik", {}).get("value", "") for r in results}
        m3.metric("🏷️ Topik Unik", len([t for t in topik_set if t]))

        st.markdown("---")

        # Kumpulkan frasa untuk RAG
        frasa_map = {}
        if enable_ai:
            with st.spinner("Mengambil data frasa untuk AI..."):
                for row in results:
                    uri = row["hadis"]["value"]
                    frasa_map[uri] = get_frasa_for_hadis(uri)

        # Render kartu hadis
        for idx, row in enumerate(results, 1):
            render_hadis_card(row, idx, show_frasa=True)

        # ── AI SYARAH (RAG) ──
        if enable_ai:
            st.markdown("---")
            st.markdown("## 🤖 AI Syarah — Powered by RAG + Groq LLaMA")
            st.markdown("""
<div style="background:#132118; border:1px solid #4CAF50; border-radius:10px; padding:16px; margin-bottom:16px;">
  <p style="color:#a5d6a7; margin:0; font-size:14px;">
    💡 <strong>Tentang Fitur Ini:</strong> AI menggunakan teknik <em>Retrieval-Augmented Generation (RAG)</em> — 
    data hadis diambil langsung dari Knowledge Graph RDF melalui SPARQL, lalu dijadikan konteks 
    untuk Groq LLaMA 3.3 70B. Hasilnya akurat dan terhindar dari halusinasi AI.
  </p>
</div>
""", unsafe_allow_html=True)

            if st.button("✨ Generate Syarah dengan AI", use_container_width=True, key="btn_syarah"):
                with st.spinner("AI sedang menganalisis hadis berdasarkan knowledge graph..."):
                    context = build_context_from_results(results, frasa_map)
                    syarah  = generate_syarah(keyword, context)
                    st.session_state.syarah_text    = syarah
                    st.session_state.syarah_context = context

            if st.session_state.syarah_text:
                if st.session_state.groq_model_used:
                    st.caption(f"✅ Model digunakan: `{st.session_state.groq_model_used}`")

                st.markdown("""
<div style="background:linear-gradient(135deg,#0d2818,#1a3a2a); border:1px solid #66bb6a;
            border-radius:12px; padding:24px; margin-top:8px;">
""", unsafe_allow_html=True)
                st.markdown(st.session_state.syarah_text)
                st.markdown("</div>", unsafe_allow_html=True)

                with st.expander("🔬 Lihat Konteks RAG yang Dikirim ke AI"):
                    st.code(st.session_state.syarah_context, language="text")

    elif keyword and mode != "📚 Semua Hadis" and st.session_state.search_mode != "":
        st.warning(f"Tidak ada hadis yang ditemukan untuk pencarian: **{keyword}**")
        st.markdown("💡 Coba kata kunci lain seperti: `niat`, `iman`, `amal`, `ikhlas`, `saudara`")

    # ── FOOTER ──
    st.markdown("---")
    st.markdown("""
<p style="text-align:center; color:#4CAF50; font-size:12px;">
  Sistem Pencarian Semantik Hadis Arbain Nawawi · Proyek Akhir Semantic Web · UNPAD<br>
  Fadhila Latsa Tsabita · Azmi Naifah Itinah · Ammara Azwadiena Alfiantie
</p>
""", unsafe_allow_html=True)

if __name__ == "__main__":
    main()