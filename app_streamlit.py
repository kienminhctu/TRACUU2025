# app_streamlit.py
"""
Streamlit app: Tra cứu câu hỏi & đáp án (SQLite FTS5 backend)
- Nếu có questions.db trong thư mục, dùng luôn.
- Nếu upload file .xlsx hoặc đặt Ngan_hang_cau_hoi.xlsx, sẽ chuyển sang SQLite (FTS) và index.
- UI: tìm kiếm FTS, filter category, tìm theo ID, pagination, highlight, download CSV.
"""

import streamlit as st
from pathlib import Path
import sqlite3, io, re, tempfile, os
import pandas as pd
import unicodedata
from typing import List, Dict

st.set_page_config(page_title="Tra cứu câu hỏi", layout="wide")

# --- Config ---
DEFAULT_XLSX = "Ngan_hang_cau_hoi.xlsx"
DB_FILE = Path("questions.db")
REQUIRED_COLS = ["ID","category","question","option_a","option_b","option_c","option_d","correct"]
PAGE_SIZE_DEFAULT = 20

# --- Utils ---
def normalize_text(s: str) -> str:
    if s is None: return ""
    s = str(s).strip().lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

def safe_str(x):
    return "" if x is None else str(x)

def strip_choice_prefix(text, expected_letter: str):
    if text is None: return ""
    s = str(text).lstrip()
    pat = rf'^(?:{expected_letter}|{expected_letter.lower()})\s*[\.\)\:\-–\/]\s*'
    return re.sub(pat, "", s, count=1)

# --- DB functions ---
@st.cache_resource
def get_conn(db_path: str = str(DB_FILE)):
    # returns sqlite3.Connection
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def has_fts5(conn) -> bool:
    try:
        cur = conn.cursor()
        cur.execute("SELECT sqlite_version()")
        # try creating temp fts table (wrap in transaction and drop)
        cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS __ftstest USING fts5(content)")
        cur.execute("DROP TABLE IF EXISTS __ftstest")
        conn.commit()
        return True
    except Exception:
        return False

def create_schema(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id TEXT,
        sheet TEXT,
        category TEXT,
        question TEXT,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        correct TEXT
    )
    """)
    # create FTS table if supported
    try:
        cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS qfts USING fts5(question, option_a, option_b, option_c, option_d, content='questions', content_rowid='rowid')")
    except Exception:
        # FTS5 not supported — caller should fallback
        pass
    conn.commit()

def clear_and_insert_questions(conn, records: List[Dict]):
    cur = conn.cursor()
    cur.execute("DELETE FROM questions")
    conn.commit()
    insert_sql = "INSERT INTO questions (id, sheet, category, question, option_a, option_b, option_c, option_d, correct) VALUES (?,?,?,?,?,?,?,?,?)"
    for r in records:
        cur.execute(insert_sql, (
            safe_str(r.get("ID")),
            safe_str(r.get("sheet")),
            safe_str(r.get("category")),
            safe_str(r.get("question")),
            safe_str(r.get("option_a")),
            safe_str(r.get("option_b")),
            safe_str(r.get("option_c")),
            safe_str(r.get("option_d")),
            safe_str(r.get("correct")),
        ))
    conn.commit()
    # populate FTS table if exists
    try:
        cur.execute("DELETE FROM qfts")
        cur.execute("INSERT INTO qfts(rowid, question, option_a, option_b, option_c, option_d) SELECT rowid, question, option_a, option_b, option_c, option_d FROM questions")
        conn.commit()
    except Exception:
        # FTS not available; ignore
        pass

def read_excel_to_records(xlsx_path) -> (List[Dict], List[str]):
    # returns (records_list, valid_sheets)
    df_dict = pd.read_excel(xlsx_path, sheet_name=None, dtype=str)
    records = []
    valid_sheets = []
    for sh, df in df_dict.items():
        if str(sh).startswith("_"):
            continue
        if df is None or df.shape[0] == 0:
            continue
        # normalize column names
        df.columns = [str(c).strip() for c in df.columns]
        if not all(col in df.columns for col in REQUIRED_COLS):
            continue
        valid_sheets.append(sh)
        for _, row in df.iterrows():
            qtext = safe_str(row.get("question")).strip()
            if not qtext:
                continue
            rec = {
                "sheet": sh,
                "ID": safe_str(row.get("ID")).strip(),
                "category": safe_str(row.get("category")).strip(),
                "question": qtext,
                "option_a": strip_choice_prefix(row.get("option_a"), "A"),
                "option_b": strip_choice_prefix(row.get("option_b"), "B"),
                "option_c": strip_choice_prefix(row.get("option_c"), "C"),
                "option_d": strip_choice_prefix(row.get("option_d"), "D"),
                "correct": safe_str(row.get("correct")).strip().upper(),
            }
            records.append(rec)
    return records, valid_sheets

# --- Search functions ---
def search_fts(conn, query: str, category: str = None, limit: int = 500):
    q = query.strip()
    cur = conn.cursor()
    # if no query, simple select
    if q == "":
        if category and category != "(Tất cả)":
            cur.execute("SELECT rowid, * FROM questions WHERE category = ? LIMIT ?", (category, limit))
        else:
            cur.execute("SELECT rowid, * FROM questions LIMIT ?", (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    # Use FTS if available
    try:
        # Prepare FTS MATCH pattern: allow phrase and AND by default
        match_query = q.replace("'", "''")
        if category and category != "(Tất cả)":
            sql = "SELECT q.rowid, q.id, q.sheet, q.category, q.question, q.option_a, q.option_b, q.option_c, q.option_d, q.correct FROM qfts JOIN questions q ON q.rowid = qfts.rowid WHERE q.category = ? AND qfts MATCH ? LIMIT ?"
            cur.execute(sql, (category, match_query, limit))
        else:
            sql = "SELECT q.rowid, q.id, q.sheet, q.category, q.question, q.option_a, q.option_b, q.option_c, q.option_d, q.correct FROM qfts JOIN questions q ON q.rowid = qfts.rowid WHERE qfts MATCH ? LIMIT ?"
            cur.execute(sql, (match_query, limit))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # fallback to simple LIKE-based token AND search
        tokens = [t for t in re.split(r"\s+", normalize_text(q)) if t]
        if not tokens:
            return []
        base_sql = "SELECT rowid, * FROM questions WHERE "
        clauses = []
        params = []
        if category and category != "(Tất cả)":
            clauses.append("category = ?")
            params.append(category)
        for t in tokens:
            clauses.append("(lower(question) LIKE ? OR lower(option_a) LIKE ? OR lower(option_b) LIKE ? OR lower(option_c) LIKE ? OR lower(option_d) LIKE ?)")
            for _ in range(5):
                params.append(f"%{t}%")
        sql = base_sql + " AND ".join(clauses) + " LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def get_all_categories(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT category FROM questions WHERE category IS NOT NULL AND category != ''")
        rows = cur.fetchall()
        cats = [r[0] for r in rows]
        cats = sorted([c for c in cats if c])
        return cats
    except Exception:
        return []

def get_by_id(conn, id_text):
    cur = conn.cursor()
    t = id_text.strip()
    # try both id and sheet-id
    cur.execute("SELECT rowid, * FROM questions WHERE id = ? LIMIT 1", (t,))
    r = cur.fetchone()
    if r: return dict(r)
    # try sheet-id pattern: "De3-123"
    if "-" in t:
        try_sh, try_id = t.split("-", 1)
        cur.execute("SELECT rowid, * FROM questions WHERE sheet = ? AND id = ? LIMIT 1", (try_sh, try_id))
        r = cur.fetchone()
        if r: return dict(r)
    return None

# --- UI helpers ---
def highlight(text: str, keyword: str) -> str:
    if not keyword:
        return text
    # escape regex metachars
    kw = re.escape(keyword)
    try:
        return re.sub(f"({kw})", r"<mark>\1</mark>", text, flags=re.I)
    except re.error:
        return text

def records_to_df(recs: List[Dict]) -> pd.DataFrame:
    if not recs:
        return pd.DataFrame(columns=["sheet","ID","category","question","option_a","option_b","option_c","option_d","correct"])
    df = pd.DataFrame(recs)
    # ensure columns order
    cols = ["sheet","ID","category","question","option_a","option_b","option_c","option_d","correct"]
    df = df.loc[:, [c for c in cols if c in df.columns]]
    return df

# --- App layout ---
st.title("🔎 Tra cứu câu hỏi & đáp án (Streamlit)")

with st.sidebar:
    st.header("Dữ liệu / Index")
    uploaded = st.file_uploader("Upload file Excel (.xlsx) để index (tạo/ghi đè DB)", type=["xlsx"], accept_multiple_files=False)
    use_default = st.checkbox(f"Dùng file mặc định `{DEFAULT_XLSX}` nếu có", value=True)
    if st.button("Tạo/ghi lại index từ file"):
        # create DB from uploaded or default
        src = None
        if uploaded is not None:
            src = uploaded
        else:
            p = Path(DEFAULT_XLSX)
            if p.exists():
                src = p
        if src is None:
            st.warning("Không tìm thấy file upload hoặc file mặc định.")
        else:
            try:
                # read records
                if hasattr(src, "read"):
                    # uploaded BytesIO
                    bytes_io = io.BytesIO(src.read())
                    records, sheets = read_excel_to_records(bytes_io)
                else:
                    records, sheets = read_excel_to_records(src)
                if not records:
                    st.error("Không có record hợp lệ trong file hoặc thiếu header required.")
                else:
                    # ensure DB file exists
                    conn = sqlite3.connect(str(DB_FILE))
                    create_schema(conn)
                    clear_and_insert_questions(conn, records)
                    conn.close()
                    # clear cached conn and recreate
                    if "get_conn" in st.session_state:
                        st.session_state.pop("get_conn", None)
                    st.success(f"Đã index {len(records)} câu từ {len(sheets)} sheet → {DB_FILE}")
            except Exception as e:
                st.error(f"Lỗi khi index: {e}")

    st.markdown("---")
    st.markdown("Nếu bạn không index file, app sẽ sử dụng `questions.db` nếu có.")
    st.markdown("Gợi ý: dùng file nhỏ hoặc để người dùng upload để tránh lưu file lớn trong repo.")
    st.markdown("---")
    st.caption("Bạn có thể upload file mới và click 'Tạo/ghi lại index' để cập nhật data.")

# Load DB or index from default if exists
db_exists = DB_FILE.exists()
if not db_exists and use_default and Path(DEFAULT_XLSX).exists():
    # auto index default file
    try:
        records, sheets = read_excel_to_records(DEFAULT_XLSX)
        if records:
            conn_tmp = sqlite3.connect(str(DB_FILE))
            create_schema(conn_tmp)
            clear_and_insert_questions(conn_tmp, records)
            conn_tmp.close()
            db_exists = True
    except Exception:
        db_exists = False

if not db_exists:
    st.warning("Chưa có database index. Upload file Excel rồi 'Tạo/ghi lại index' hoặc đặt questions.db/Ngan_hang_cau_hoi.xlsx vào thư mục.")
    # still allow continue but search will return empty
else:
    conn = get_conn()
    # verify schema
    create_schema(conn)

# --- Search controls ---
col1, col2, col3 = st.columns([4,2,2])
with col1:
    query = st.text_input("Từ khóa tìm (FTS hỗ trợ phrase / AND / OR). Để trống để hiện tất cả:", "")
with col2:
    id_search = st.text_input("Tìm theo ID (ví dụ De3-123 hoặc 123):", "")
with col3:
    page_size = st.selectbox("Bản ghi / trang", options=[10,20,50,100], index=1)

# category filter
all_categories = []
if DB_FILE.exists():
    try:
        all_categories = get_all_categories(get_conn())
    except Exception:
        all_categories = []
cat_choice = st.selectbox("Lọc theo nhóm (category)", options=["(Tất cả)"] + all_categories)

# --- Execute search ---
results = []
if id_search.strip():
    if DB_FILE.exists():
        r = get_by_id(get_conn(), id_search.strip())
        if r:
            results = [r]
        else:
            st.info("Không tìm thấy ID.")
            results = []
    else:
        st.info("DB chưa có; không thể tìm ID.")
        results = []
else:
    if DB_FILE.exists():
        results = search_fts(get_conn(), query, category=cat_choice, limit=2000)
    else:
        results = []

st.write(f"**Kết quả: {len(results)} bản ghi**")

# Pagination
total = len(results)
total_pages = max(1, (total + page_size - 1) // page_size)
if 'page' not in st.session_state:
    st.session_state.page = 1
# reset page if smaller result
if st.session_state.page > total_pages:
    st.session_state.page = 1

coln1, coln2, coln3 = st.columns([1,1,8])
with coln1:
    if st.button("« Trước") and st.session_state.page > 1:
        st.session_state.page -= 1
with coln2:
    if st.button("Sau »") and st.session_state.page < total_pages:
        st.session_state.page += 1
with coln3:
    st.write(f"Trang {st.session_state.page} / {total_pages}")

start = (st.session_state.page - 1) * page_size
end = start + page_size
page_items = results[start:end]

# Left: list / grid; Right: detail
left, right = st.columns([2,4])
with left:
    st.subheader("Danh sách kết quả")
    # try aggrid if installed
    use_ag = False
    try:
        from st_aggrid import AgGrid, GridOptionsBuilder
        use_ag = True
    except Exception:
        use_ag = False

    df_page = records_to_df(page_items)
    if df_page.empty:
        st.write("Không có kết quả để hiển thị.")
    else:
        if use_ag:
            gb = GridOptionsBuilder.from_dataframe(df_page[["sheet","ID","category","question"]])
            gb.configure_selection(selection_mode="single", use_checkbox=False)
            gb.configure_column("question", wrapText=True, autoHeight=True)
            grid_resp = AgGrid(df_page, gridOptions=gb.build(), height=400, enable_enterprise_modules=False)
            selected = grid_resp.get("selected_rows", [])
            if selected:
                sel_row = selected[0]
                # find index in results
                # grid returns columns so map back
                sel_idx = None
                for i, r in enumerate(results):
                    if r["sheet"] == sel_row["sheet"] and str(r["ID"]) == str(sel_row["ID"]):
                        sel_idx = i
                        break
                if sel_idx is not None:
                    st.session_state.selected_idx = sel_idx
        else:
            # simple clickable radio/selectbox
            titles = [f"{r['sheet']} | ID {r['ID']} | {r['question'][:80].replace(chr(10),' ')}" for r in page_items]
            choice = st.radio("Chọn 1:", options=list(range(len(page_items))), format_func=lambda i: titles[i])
            # map to global index
            st.session_state.selected_idx = start + choice

with right:
    sel = st.session_state.get("selected_idx", start if page_items else None)
    if sel is None:
        if page_items:
            sel = start
            st.session_state.selected_idx = sel
    if sel is None or sel >= len(results):
        st.info("Chưa có bản ghi hợp lệ để hiển thị.")
    else:
        r = results[sel]
        st.subheader(f"[{r.get('sheet')}] ID: {r.get('ID')}  —  Nhóm: {r.get('category')}")
        # highlight question
        st.markdown("**Câu hỏi:**")
        st.markdown(highlight(r.get("question",""), query), unsafe_allow_html=True)
        st.markdown("**Đáp án:**")
        opts = [("A", r.get("option_a","")), ("B", r.get("option_b","")), ("C", r.get("option_c","")), ("D", r.get("option_d",""))]
        for k,val in opts:
            if k == (r.get("correct") or "").upper():
                st.markdown(f"<div style='background:#ecfdf5;padding:6px;border-radius:6px'><b>→ {k}. {highlight(val, query)}</b></div>", unsafe_allow_html=True)
            else:
                st.markdown(f"{k}. {highlight(val, query)}", unsafe_allow_html=True)
        st.markdown(f"**Đáp án đúng:** `{r.get('correct')}`")

        # download / copy
        detail_text = f"[{r.get('sheet')}] ID: {r.get('ID')} | Nhóm: {r.get('category')}\n\n{r.get('question')}\n\n"
        for k, val in opts:
            prefix = "→" if k == (r.get("correct") or "").upper() else "  "
            detail_text += f"{prefix} {k}. {val}\n"
        detail_text += f"\nĐáp án đúng: {r.get('correct')}\n"

        st.download_button("Tải câu chi tiết (TXT)", data=detail_text, file_name=f"detail_{r.get('sheet')}_{r.get('ID')}.txt", mime="text/plain")

        # copy to clipboard via JS (works in supported browsers)
        copy_html = f"""
        <textarea id="txt_{sel}" style="display:none;">{detail_text.replace('&','&amp;').replace('<','&lt;')}</textarea>
        <button onclick="const t=document.getElementById('txt_{sel}'); navigator.clipboard.writeText(t.value).then(()=>{{alert('Đã sao chép vào clipboard')}}).catch(()=>{{alert('Không thể copy - trình duyệt không hỗ trợ')}})">Sao chép câu/đáp án</button>
        """
        st.components.v1.html(copy_html, height=50)

# Download entire current results
if total > 0:
    df_all = records_to_df(results)
    csv_bytes = df_all.to_csv(index=False).encode('utf-8')
    st.download_button("Tải toàn bộ kết quả (CSV)", data=csv_bytes, file_name="ketqua_tracuu.csv", mime="text/csv")

st.markdown("---")
st.caption("Gợi ý: Upload file Excel và click 'Tạo/ghi lại index' để cập nhật dữ liệu. "
           "Để hoạt động tốt với dataset lớn, hãy chạy convert_xlsx_to_sqlite.py offline và commit questions.db hoặc lưu DB trên storage phù hợp.")

# --- end ---
