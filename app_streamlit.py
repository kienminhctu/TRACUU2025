import streamlit as st
from pathlib import Path
import unicodedata, re, io
import pandas as pd

# --- Cấu hình ---
DEFAULT_FILE = "Ngan_hang_cau_hoi.xlsx"
REQUIRED_COLS = ["ID","category","question","option_a","option_b","option_c","option_d","correct"]

st.set_page_config(page_title="Tra cứu câu hỏi", layout="wide")

# ------------- tiện ích -------------
def normalize(text: str) -> str:
    if text is None: return ""
    s = str(text).lower().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

def strip_choice_prefix(text, expected_letter: str):
    if text is None: return ""
    s = str(text).lstrip()
    pat = rf'^(?:{expected_letter}|{expected_letter.lower()})\s*[\.\)\:\-–\/]\s*'
    return re.sub(pat, "", s, count=1)

def read_excel_sheets(path: Path):
    # đọc tất cả sheet, trả về list các bản ghi giống cấu trúc trong app Tk
    recs = []
    try:
        sheets = pd.read_excel(path, sheet_name=None, dtype=str)  # dict sheetname -> df
    except Exception as e:
        st.error(f"Không thể đọc file Excel: {e}")
        return recs, []
    valid_sheets = []
    for sh, df in sheets.items():
        # bỏ sheet trống / tên bắt đầu bằng "_" tương tự bản gốc
        if sh.startswith("_"): 
            continue
        if df.shape[0] == 0 or df.shape[1] == 0:
            continue
        cols = [str(c).strip() for c in df.columns]
        if any(c not in cols for c in REQUIRED_COLS):
            # không hợp chuẩn -> bỏ qua sheet
            continue
        valid_sheets.append(sh)
        # đảm bảo lấy bằng tên cột chính xác
        df = df.rename(columns={c: str(c).strip() for c in df.columns})
        for _, row in df.iterrows():
            qtext = str(row.get("question") or "").strip()
            if not qtext: 
                continue
            r = {
                "sheet": sh,
                "ID": str(row.get("ID") or "").strip(),
                "category": str(row.get("category") or "").strip(),
                "question": qtext,
                "option_a": strip_choice_prefix(row.get("option_a") or "", "A"),
                "option_b": strip_choice_prefix(row.get("option_b") or "", "B"),
                "option_c": strip_choice_prefix(row.get("option_c") or "", "C"),
                "option_d": strip_choice_prefix(row.get("option_d") or "", "D"),
                "correct": str(row.get("correct") or "").strip().upper(),
            }
            r["_index"] = normalize(" ".join([
                r["question"], r["option_a"], r["option_b"], r["option_c"], r["option_d"]
            ]))
            recs.append(r)
    return recs, valid_sheets

# ------------- UI -------------
st.title("🔎 Tra cứu câu hỏi & đáp án (Streamlit)")
st.caption("Dựa trên app Tkinter gốc — đọc nhiều sheet, tìm theo từ khoá, tìm theo ID, tải CSV.")

# Sidebar: upload / dùng file mặc định
with st.sidebar:
    st.header("Dữ liệu")
    uploaded = st.file_uploader("Upload file Excel (.xlsx) (nếu muốn dùng file này)", type=["xlsx","xls"])
    use_default = st.checkbox(f"Dùng file mặc định `{DEFAULT_FILE}` nếu có", value=True)
    st.markdown("---")
    st.markdown("Gợi ý: file nên có các cột: " + ", ".join(REQUIRED_COLS))
    st.markdown("Sheet có tên bắt đầu `_` sẽ bị bỏ qua (dùng cho metadata).")

# Load dữ liệu
records = []
sheets_used = []
if uploaded is not None:
    # đọc từ bytes
    bytes_io = io.BytesIO(uploaded.read())
    records, sheets_used = read_excel_sheets(bytes_io)
else:
    default_path = Path(DEFAULT_FILE)
    if use_default and default_path.exists():
        records, sheets_used = read_excel_sheets(default_path)

if not records:
    st.warning("Chưa có dữ liệu (upload file .xlsx hoặc đặt file mặc định vào thư mục).")
    st.stop()

st.sidebar.success(f"Đã nạp {len(records)} câu từ {len(sheets_used)} sheet")

# Search & filters
col1, col2 = st.columns([3,1])
with col1:
    query = st.text_input("Nhập từ khóa (gõ một phần câu hỏi, nhiều từ cách nhau sẽ AND):", "")
with col2:
    id_search = st.text_input("Tìm theo ID (ví dụ De3-123 hoặc 123):", "")

# Lọc theo category nếu có
categories = sorted({r["category"] for r in records if r["category"]})
cat_choice = st.selectbox("Lọc theo nhóm (category)", options=["(Tất cả)"] + categories, index=0)

# Tùy chọn kết quả
page_size = st.selectbox("Số bản ghi / trang", [10,20,50,100], index=1)

# ------------- logic tìm -------------
def filter_by_query(recs, q, cat):
    # token AND search trên _index (đã normalize)
    if cat and cat != "(Tất cả)":
        recs = [r for r in recs if r["category"] == cat]
    qn = normalize(q)
    tokens = [t for t in re.split(r"\s+", qn) if t]
    if not tokens:
        return recs
    out = []
    for r in recs:
        txt = r["_index"]
        ok = True
        for t in tokens:
            if t not in txt:
                ok = False; break
        if ok: out.append(r)
    return out

# apply id search if provided — id_search có độ ưu tiên: nếu có, hiển thị kết quả ID
results = records
if id_search and id_search.strip():
    t = id_search.strip()
    # normalize forms: sheet-ID or ID
    found = []
    for r in records:
        if r["ID"] == t or f"{r['sheet']}-{r['ID']}" == t:
            found.append(r)
    if not found:
        st.info(f"Không tìm thấy ID: {t}")
        results = []
    else:
        results = found
else:
    results = filter_by_query(records, query, cat_choice)

st.write(f"**Kết quả: {len(results)} bản ghi**")

# Pagination
total = len(results)
total_pages = max(1, (total + page_size - 1) // page_size)
if 'page' not in st.session_state:
    st.session_state.page = 1
# navigation
coln1, coln2, coln3 = st.columns([1,1,6])
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

# Left column: danh sách rút gọn; Right: chi tiết
left, right = st.columns([2,4])
with left:
    st.subheader("Danh sách (chọn 1 để xem chi tiết)")
    # show short titles with index
    for i, r in enumerate(page_items):
        label = f"{r['sheet']} | ID {r['ID']} | {r['question'][:80].replace(chr(10),' ')}"
        if st.button(label, key=f"btn_{start+i}"):
            st.session_state.selected_idx = start + i

    if total == 0:
        st.write("Không có kết quả.")

with right:
    sel = st.session_state.get("selected_idx", start if page_items else None)
    if sel is None or sel >= len(results) + start:
        # default show first of current page if exists
        if page_items:
            sel = start
            st.session_state.selected_idx = sel
        else:
            st.info("Không có bản ghi để hiển thị.")
            st.stop()
    r = results[sel - start] if sel >= start and sel < end else results[sel] if sel < len(results) else None
    if r is None:
        st.info("Chưa chọn bản ghi hợp lệ.")
    else:
        st.subheader(f"[{r['sheet']}] ID: {r['ID']}  —  Nhóm: {r['category']}")
        st.markdown("**Câu hỏi:**")
        st.write(r['question'])
        opts = [("A", r["option_a"]), ("B", r["option_b"]), ("C", r["option_c"]), ("D", r["option_d"])]
        for k, val in opts:
            if k == r["correct"]:
                st.markdown(f"<div style='background:#ecfdf5;padding:6px;border-radius:6px'><b>→ {k}. {val}</b></div>", unsafe_allow_html=True)
            else:
                st.markdown(f"{k}. {val}")
        st.markdown(f"**Đáp án đúng:** `{r['correct']}`")

        # render plain text for copy/download
        detail_text = f"[{r['sheet']}] ID: {r['ID']} | Nhóm: {r['category']}\n\n{r['question']}\n\n"
        for k, val in opts:
            prefix = "→" if k == r["correct"] else "  "
            detail_text += f"{prefix} {k}. {val}\n"
        detail_text += f"\nĐáp án đúng: {r['correct']}\n"

        # Copy to clipboard (via tiny HTML/JS)
        copy_html = f"""
        <textarea id="txt" style="display:none;">{detail_text.replace('&','&amp;').replace('<','&lt;')}</textarea>
        <button onclick="const t=document.getElementById('txt'); navigator.clipboard.writeText(t.value).then(()=>{{alert('Đã sao chép vào clipboard')}}) ">Sao chép câu/đáp án vào clipboard</button>
        """
        st.components.v1.html(copy_html, height=60)

# Download whole results as CSV
if total > 0:
    df_out = pd.DataFrame(results)
    csv_bytes = df_out.to_csv(index=False).encode('utf-8')
    st.download_button("Tải kết quả (CSV)", data=csv_bytes, file_name="ketqua_tracuu.csv", mime="text/csv")

# Footer: small tips
st.markdown("---")
st.caption("Gợi ý: để tìm chính xác ID nhập 'De3-123' hoặc '123' (nếu ID duy nhất).")
