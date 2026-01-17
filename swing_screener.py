import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import io
import time
import re

# --- 設定 ---
st.set_page_config(page_title="プロ株分析ハイブリッド", layout="wide")

import streamlit.components.v1 as components

# --- PWA設定の注入 ---
def inject_pwa_meta():
    pwa_meta = """
    <link rel="manifest" href="https://raw.githubusercontent.com/あなたのユーザー名/リポジトリ名/main/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black">
    <meta name="apple-mobile-web-app-title" content="株スキャナー">
    <link rel="apple-touch-icon" href="https://cdn-icons-png.flaticon.com/512/2534/2534185.png">
    """
    # st.markdown(pwa_meta, unsafe_allow_html=True) # これだけでは不十分な場合があるため
    components.html(f"<script>window.parent.document.head.insertAdjacentHTML('beforeend', `{pwa_meta}`);</script>", height=0)

inject_pwa_meta()

# --- データ取得 ---
@st.cache_data(ttl=86400)
def get_jpx_master():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        df = pd.read_excel(io.BytesIO(res.content))
        df.columns = df.columns.str.replace('\n', '').str.strip()
        df = df[['コード', '銘柄名', '17業種区分', '市場・商品区分']].dropna()
        df['ticker'] = df['コード'].astype(str) + ".T"
        return df
    except Exception as e:
        st.error(f"銘柄リスト取得エラー: {e}")
        return pd.DataFrame()

def get_market_status():
    indices = {"^N225": "日経平均", "^GSPC": "S&P500", "JPY=X": "ドル円", "^VIX": "VIX指数"}
    data = yf.download(list(indices.keys()), period="5d", interval="1d")['Close']
    return data, indices

def get_margin_ratio(code):
    url = f"https://kabutan.jp/stock/kabuka?code={code}"
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        pattern = r'信用倍率</th><td>([\d\.]+)<span>倍</span>'
        match = re.search(pattern, res.text)
        return float(match.group(1)) if match else None
    except: return None

# --- 分析ロジック ---
def analyze_stock(df, mode):
    if len(df) < 30: return None
    close = df['Close']
    vol = df['Volume']

    # 共通指標
    ma5 = close.rolling(5).mean()
    ma25 = close.rolling(25).mean()
    current_price = close.iloc[-1]

    # 売買代金 (直近)
    trading_value = (current_price * vol.iloc[-1]) / 10**8 # 億円

    signals = []

    if mode == "デイトレ":
        # デイトレ用ロジック：勢いと流動性
        vol_ratio = vol.iloc[-1] / vol.iloc[-5:-1].mean()
        day_change = ((current_price / close.iloc[-2]) - 1) * 100

        if trading_value < 10: return None # 流動性不足（10億以下）
        if vol_ratio > 2.0: signals.append("🚀急騰中")
        if 2.0 < day_change < 5.0: signals.append("📈好位置")

        return {
            "判定": " / ".join(signals) if signals else "静観",
            "売買代金(億)": round(trading_value, 1),
            "出来高倍率": round(vol_ratio, 2),
            "RSI": "-", # デイトレでは重視しないため
            "需給": "-"
        }

    else: # スイングモード
        # スイング用ロジック：トレンドと需給
        vol_ratio = vol.iloc[-1] / vol.iloc[-10:-1].mean()
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]

        if ma5.iloc[-2] <= ma25.iloc[-2] and ma5.iloc[-1] > ma25.iloc[-1]:
            signals.append("✨GC")
        if rsi < 35: signals.append("💎底値圏")

        return {
            "判定": " / ".join(signals) if signals else "保合い",
            "売買代金(億)": round(trading_value, 1),
            "出来高倍率": round(vol_ratio, 2),
            "RSI": round(rsi, 1),
            "需給": "要確認" # 後ほど信用倍率で上書き
        }

# --- UI構築 ---
st.title("🚀 株トレード・マルチ戦略ボード")

# Step 1: 市場概況
with st.expander("🌍 Step 1: 市場トレンド確認", expanded=True):
    m_data, m_indices = get_market_status()
    cols = st.columns(len(m_indices))
    for i, (ticker, name) in enumerate(m_indices.items()):
        change = ((m_data[ticker].iloc[-1] / m_data[ticker].iloc[-2]) - 1) * 100
        cols[i].metric(name, f"{m_data[ticker].iloc[-1]:.1f}", f"{change:.2f}%")

# サイドバー設定
master_df = get_jpx_master()
with st.sidebar:
    st.header("🛠 戦略設定")
    trade_mode = st.selectbox("トレードスタイル", ["スイング", "デイトレ"])

    selected_sector = st.selectbox("セクター", sorted(master_df['17業種区分'].unique().tolist()))

    vol_target = st.slider("最低出来高倍率", 1.0, 5.0, 1.3)

    if trade_mode == "デイトレ":
        st.caption("※デイトレ：売買代金と当日の勢いを重視")
    else:
        st.caption("※スイング：需給(信用)とMAトレンドを重視")

# 実行
if st.button(f"{selected_sector} を {trade_mode}視点でスキャン"):
    target_stocks = master_df[master_df['17業種区分'] == selected_sector]
    progress_bar = st.progress(0)
    hit_results = []

    status_msg = st.empty()
    status_msg.text("データ一括取得中...")

    all_data = yf.download(target_stocks['ticker'].tolist(), period="40d", group_by='ticker', threads=True)

    for i, row in enumerate(target_stocks.itertuples()):
        try:
            hist = all_data[row.ticker].dropna()
            res = analyze_stock(hist, trade_mode)

            if res and res['出来高倍率'] >= vol_target:
                # スイング時のみ重い信用データを取得
                if trade_mode == "スイング":
                    m_ratio = get_margin_ratio(row.コード)
                    res['需給'] = "🔥踏み上げ" if m_ratio and m_ratio < 0.7 else ("⚠️重い" if m_ratio and m_ratio > 10 else f"{m_ratio}倍")

                hit_results.append({
                    "コード": row.コード,
                    "銘柄名": row.銘柄名,
                    "現在値": round(hist['Close'].iloc[-1], 1),
                    "前日比%": round(((hist['Close'].iloc[-1] / hist['Close'].iloc[-2]) - 1) * 100, 2),
                    "代金(億)": res['売買代金(億)'],
                    "出来高倍率": res['出来高倍率'],
                    "RSI": res['RSI'],
                    "需給/倍率": res['需給'],
                    "判定": res['判定'],
                    "詳細": f"https://kabutan.jp/stock/?code={row.コード}"
                })
                time.sleep(0.2) # 株探負荷軽減
        except: continue
        finally: progress_bar.progress((i+1)/len(target_stocks))

    status_msg.success(f"スキャン完了！ ({trade_mode}モード)")

    if hit_results:
        df_res = pd.DataFrame(hit_results)
        st.dataframe(
            df_res.sort_values("出来高倍率", ascending=False),
            column_config={"詳細": st.column_config.LinkColumn("株探")},
            use_container_width=True, hide_index=True
        )
    else:
        st.warning("条件に合う銘柄が見つかりませんでした。")
