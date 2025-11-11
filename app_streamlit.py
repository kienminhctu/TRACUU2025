# app_streamlit.py  — Minimal, robust Streamlit search app (pandas, no sqlite)
import streamlit as st
import pandas as pd
import unicodedata, re, io
from pathlib import Path

st.set_page_config(page_title="Tra cứu câu hỏi (simple)", layout="wide")

DEFAULT_XLSX = "Ngan_hang_cau_hoi.xlsx"
REQUIRED = ["ID","category","question","option_a","option_b","option_c","option_d","correct"]

def normalize_text(s: str) -> str:
    if s is None: return ""
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

@st.cache_data
def load_excel(path_or_bytes):
    # path_or_bytes may be a Path/str or a BytesIO (uploaded file)
    try:
        if isinstance(path_or_bytes, (str, Path)):
            x = pd.read_excel(path_or_bytes, sheet_name=None, dtype=str)
        else:
            # BytesIO
            x = pd.read_excel(path_or_bytes, sheet_name=None, dtype=str)
    except Exception as e:
        raise
    records = []
    for sh, df in x.items():
        if str(sh).startswith("_"): continue
        if df is None or df.shape[0] == 0: continue
        df.columns = [str(c).strip() for c in df.columns]
        if not all(c in df.columns for c in REQUIRED):
            # skip sheet if missing columns
            continue
        for _, row in df.iterrows():
            q = str(row.get("question") or "").strip()
            if not q: continue
            rec = {
                "sheet": sh,
                "ID": str(row.get("ID") or "").strip(),
                "category": str(row.get("category") or "").strip(),
                "question": q,
                "option_a": str(row.get("option_a") or "").strip(),
                "option_b": str(row.get("option_b") or "").strip(),
                "option_c": str(row.get("option_c") or "").strip(),
                "option_d": str(row.get("option_d") or "").strip(),
                "correct": str(row.get("correct") or "").strip().upper(),
            }
            rec["_search"] = normalize_text(" ".join([rec["question"], rec["option_a"], rec["option_b"], rec["option_c"], rec["option_d"]]))
            records.append(rec)
    return records

def search_records(records, query, category=None, limit=1000):
    qn = normalize_text(query or "")
    out = []
    for r in records:
        if category and category != "(Tất cả)" and r.get("category","") != category:
            continue
        if qn == "" or qn in r["_search"]:
            out.append(r)
        # also support exact ID search if user typed "ID:xxx" or only numbers
    return out[:limit]

# --- UI ---
st.title("🔎 Tra cứu câu hỏi & đáp án (simple)")

with st.sidebar:
    st.header("Dữ liệu")
    uploaded = st.file_uploader("Upload file Excel (.xlsx) để dùng", type=["xlsx"])
    use_default = st.checkbox(f"Dùng file mặc định `{DEFAULT_XLSX}` nếu có", value=True)
    st.markdown("---")
    st.markdown("Nếu không có file, upload file hoặc đẩy file `questions.db`/Excel vào repo.")

# Load data (uploaded first, else default file if exists)
records = []
if uploaded is not None:
    try:
        bytes_io = io.BytesIO(uploaded.read())
        records = load_excel(bytes_io)
        st.sidebar.success(f"Đã nạp {len(records)} câu từ file upload.")
    except Exception as e:
        st.sidebar.error(f"Lỗi đọc file upload: {e}")
elif use_default and Path(DEFAULT_XLSX).exists():
    try:
        records = load_excel(DEFAULT_XLSX)
        st.sidebar.success(f"Đã nạp {len(records)} câu từ `{DEFAULT_XLSX}`.")
    except Exception as e:
        st.sidebar.error(f"Lỗi đọc default file: {e}")
else:
    st.sidebar.info("Chưa nạp dữ liệu. Upload file Excel hoặc đặt file mặc định vào thư mục deploy.")

# Controls
col1, col2, col3 = st.columns([4,2,1])
with col1:
    query = st.text_input("Từ khóa tìm (viết có/không dấu):", "")
with col2:
    id_search = st.text_input("Tìm theo ID (ví dụ De3-123 hoặc 123):", "")
with col3:
    per_page = st.selectbox("Bản ghi / trang", [10,20,50], index=1)

# categories
cats = sorted(list({r.get("category","") for r in records if r.get("category","")}))
cat_choice = st.selectbox("Lọc theo nhóm (category)", options=["(Tất cả)"] + cats)

# If ID search given, try to show single
results = []
if id_search.strip():
    t = id_search.strip()
    for r in records:
        if r.get("ID") == t or f"{r.get('sheet')}-{r.get('ID')}" == t:
            results = [r]; break
else:
    results = search_records(records, query, category=cat_choice if cat_choice else None, limit=5000)

st.markdown(f"**Kết quả: {len(results)} bản ghi**")

# pagination
page = st.session_state.get("page", 1)
total = len(results)
pages = max(1, (total + per_page - 1)//per_page)
if st.button("« Trước") and page>1:
    page -= 1
    st.session_state.page = page
if st.button("Sau »") and page<pages:
    page += 1
    st.session_state.page = page
st.write(f"Trang {page} / {pages}")

start = (page-1)*per_page
page_items = results[start:start+per_page]

left, right = st.columns([2,4])
with left:
    st.subheader("Danh sách kết quả")
    if not page_items:
        st.info("Không có dữ liệu để hiển thị. Upload hoặc chọn file.")
    else:
        opts = []
        for i, r in enumerate(page_items):
            title = f"{r['sheet']} | ID {r['ID']} | {r['question'][:80].replace(chr(10),' ')}"
            if st.button(title, key=f"btn_{start+i}"):
                st.session_state["selected"] = start+i

with right:
    sel = st.session_state.get("selected", 0)
    if results and sel < len(results):
        r = results[sel]
        st.subheader(f"[{r.get('sheet')}] ID: {r.get('ID')} — Nhóm: {r.get('category')}")
        st.markdown("**Câu hỏi:**")
        st.write(r.get("question"))
        st.markdown("**Đáp án:**")
        for k,v in [("A", r.get("option_a")), ("B", r.get("option_b")), ("C", r.get("option_c")), ("D", r.get("option_d"))]:
            if k == (r.get("correct") or "").upper():
                st.success(f"{k}. {v}")
            else:
                st.write(f"{k}. {v}")
        st.markdown(f"**Đáp án đúng:** `{r.get('correct')}`")
        detail_txt = f"[{r.get('sheet')}] ID: {r.get('ID')} | Nhóm: {r.get('category')}\n\n{r.get('question')}\n\nA. {r.get('option_a')}\nB. {r.get('option_b')}\nC. {r.get('option_c')}\nD. {r.get('option_d')}\n\nĐáp án đúng: {r.get('correct')}\n"
        st.download_button("Tải câu chi tiết (TXT)", data=detail_txt, file_name=f"detail_{r.get('sheet')}_{r.get('ID')}.txt")

# allow CSV download of results
if results:
    df = pd.DataFrame(results)
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Tải toàn bộ kết quả (CSV)", data=csv, file_name="results.csv", mime="text/csv")

st.markdown("---")
st.caption("Phiên bản nhanh & an toàn: đọc Excel bằng pandas. Nếu bạn muốn, mình sẽ giúp khôi phục SQLite/FTS sau khi app ổn định.")
