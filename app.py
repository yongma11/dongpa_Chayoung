# 파일명: app.py (v10.0 후보 - 기존 app.py를 대체할 파일)
# 동파법 마스터 v10.0 - 동파법 + 카마릴라 R4 돌파매매 오버레이 콤보 (대시보드/백테스트 통합)
#
# 변경사항 (v9.8 → v10.0):
#   [신규] 카마릴라 R4 오버레이를 "별도 앱"이 아니라 기존 대시보드에 ON/OFF 토글로 통합.
#     - 이유: 동파법과 카마릴라는 real_cash를 공유하는 단일 계좌 모델 (백테스트/봇과 동일
#       아키텍처). 분리된 앱으로 만들면 같은 계좌 잔고를 두 곳에서 따로 추적해야 해서
#       괴리가 생김. settings.json 하나를 bot.py(v10.0)와 공유하므로, 여기서 토글을
#       켜면 다음 실제 매매 봇 실행 때도 자동으로 콤보 모드로 전환됨 (SSOT 유지).
#   [신규] OVERLAY_DEFAULTS: overlay_enabled=False가 기본값 → settings.json에 새 키가
#     없으면 v9.8과 완전히 동일하게 동작 (하위호환).
#   [신규] get_data_final()에 SOXL Open/High/Low 추가 (카마릴라 R4 저항선 계산용).
#   [신규] auto_sync_engine() — bot.py(v10.0)의 get_orders_and_status와 동일한 3단계
#     루프 추가: ① 전날 카마릴라 포지션 오늘 시가 청산 → ② 동파법 매도/매수(원본 그대로)
#     → ③ 남은 현금의 overlay_fraction만큼 오늘 신호 있으면 신규 진입.
#   [신규] run_backtest_fixed() — dongpa_camarilla_combo.py의 run_combined_backtest와
#     동일한 3단계 구조를 흡수. 카마릴라 거래수/승률/손익을 별도 지표로 분리해서 보여줌.
#   [신규] 사이드바에 "🧨 카마릴라 R4 오버레이" 설정 패널 (기본 꺼짐) → 저장 시
#     settings.json에 반영. 백테스트 탭에도 동일한 옵션의 체크박스를 별도로 둠
#     (백테스트는 저장된 설정과 무관하게 그 자리에서 자유롭게 켜고 끌 수 있음).
#   [범위 제한] 카마릴라는 fee/slippage(왕복)만 반영, 세금은 시뮬레이션하지 않음
#     (실제 양도세는 TaxWithdrawals 시트로 수동 추적, 기존 v9.8 방식 그대로).
#
# 변경사항 (v9.7 → v9.8): splits 이중조정 버그 수정 + Dividend 재투자 (cash 주입 방식)
# 변경사항 (v9.6 → v9.7): yf.Ticker.splits 로 수동 split 조회 (v9.8에서 제거됨)
# 변경사항 (v9.5 → v9.6): auto_adjust=False (dividend retroactive 차단)
# 변경사항 (v9.3 → v9.4): 양도세 인출 기록 시트 연동
# 변경사항 (v9.2 → v9.3): tax_strategy 옵션 (A/B/C/D)
# 변경사항 (v9.1 → v9.2): include_fees / include_tax 옵션
# 변경사항 (v9.0 → v9.1): MAX_SLOTS_OFFENSE = 6 / MAX_SLOTS_SAFE = 7
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import requests
from github import Github
from io import StringIO
import json
import time
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import gspread
from google.oauth2.service_account import Credentials
st.set_page_config(page_title="동파법 마스터 v10.0 (+카마릴라 오버레이)", page_icon="💎", layout="wide")
st.markdown("""
<style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.8/dist/web/static/pretendard.css");
    html, body, [class*="css"] { font-family: 'Pretendard', sans-serif; }
    .st-card { background-color: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); border: 1px solid #e0e0e0; margin-bottom: 15px; }
    @media (prefers-color-scheme: dark) { .st-card { background-color: #262730; border: 1px solid #41424b; } }
    .badge-buy  { background-color: #e6f4ea; color: #1e8e3e; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; }
    .badge-sell { background-color: #fce8e6; color: #d93025; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; }
    .badge-info { background-color: #e8f0fe; color: #1a73e8; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; }
    .badge-qs-low  { background-color: #e6f4ea; color: #1e8e3e; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-qs-high { background-color: #fff3e0; color: #e65100; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-qs-mid  { background-color: #f5f5f5; color: #555;    padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-ls-on   { background-color: #fce8e6; color: #d93025; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-ls-off  { background-color: #f5f5f5; color: #555;    padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-cama-open { background-color: #fff3e0; color: #e65100; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-cama-cand { background-color: #e8f0fe; color: #1a73e8; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-cama-none { background-color: #f5f5f5; color: #555;    padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    div[data-testid="stMetric"] { background-color: rgba(255, 255, 255, 0.05); border: 1px solid rgba(128, 128, 128, 0.2); padding: 15px; border-radius: 10px; text-align: center; }
</style>
""", unsafe_allow_html=True)
PARAMS = {
    'Safe':    {'buy': 3.0, 'sell': 0.5, 'time': 28, 'desc': '🛡️ 방어 (Safe)'},
    'Offense': {'buy': 3.0, 'sell': 4.0, 'time': 7,  'desc': '⚔️ 공세 (Offense)'},
}
LOCAL_PARAMS = {
    'Safe':    {'buy': 0.03, 'sell': 1.005, 'time': 28},
    'Offense': {'buy': 0.03, 'sell': 1.04,  'time': 7},
}
MAX_SLOTS_SAFE    = 7
MAX_SLOTS_OFFENSE = 6
RESET_CYCLE       = 12
def max_slots_for(mode):
    return MAX_SLOTS_OFFENSE if mode == 'Offense' else MAX_SLOTS_SAFE
QS_MA_WINDOW   = 30
QS_LOW_THRESH  = 0.75
QS_HIGH_THRESH = 1.25
QS_LOW_MULT    = 3.0
QS_HIGH_MULT   = 0.90
QS_HIGH_SLOT_CAP = 4
LOSS_STREAK_N   = 3
LOSS_STREAK_MUL = 0.8
LOSS_STREAK_WIN = 5
def loss_streak_mul(recent_outcomes):
    if len(recent_outcomes) >= LOSS_STREAK_N and \
       all(not x for x in recent_outcomes[-LOSS_STREAK_N:]):
        return LOSS_STREAK_MUL
    return 1.0
# ★ v10.0: 카마릴라 R4 오버레이 기본값 (settings.json으로 덮어쓸 수 있음, bot.py(v10.0)와 동일)
OVERLAY_DEFAULTS = {
    'overlay_enabled':     False,   # 기본 false = v9.8과 100% 동일 동작 (하위호환)
    'overlay_fraction':    0.70,
    'cama_coef':           0.70,
    'cama_vol_filter_pct': 0.80,    # None 이면 변동성 필터 미사용
    'cama_fee_rate':       0.0005,
    'cama_slippage_pct':   0.0010,
}
try:
    GH_TOKEN = st.secrets["general"]["GH_TOKEN"]
except:
    st.error("🚨 GitHub 토큰 오류: Streamlit Secrets에 GH_TOKEN을 설정해주세요.")
    st.stop()
REPO_KEY      = "yongma11/dongpa6"
HOLDINGS_FILE = "my_holdings.csv"
JOURNAL_FILE  = "trading_journal.csv"
EQUITY_FILE   = "equity_history.csv"
SETTINGS_FILE = "settings.json"
SPREADSHEET_ID        = "1s8XX-8PUAWyWOHOwst2W-b99pQo1_aFtLVg5uTD_HMI"
WITHDRAWAL_SHEET_NAME = "TaxWithdrawals"
def get_now_kst():
    return datetime.utcnow() + timedelta(hours=9)
@st.cache_data(ttl=600)
def get_data_final(period='max'):
    """★ v9.8: splits 는 yfinance Close 가 이미 처리. Dividends 는 별도 컬럼으로 가져와
    시뮬에서 cash 주입 방식으로 재투자.
    ★ v10.0: SOXL Open/High/Low 추가 (카마릴라 R4 저항선 계산용).
    - auto_adjust=False → 'Close' = split-adjusted (yfinance 자동), dividend 미반영
    - actions=True → 'Dividends' 컬럼 활용 가능
    """
    for attempt in range(3):
        try:
            start_date   = '2005-01-01'
            end_date_str = (get_now_kst() + timedelta(days=1)).strftime('%Y-%m-%d')
            df_qqq  = yf.download("QQQ",  start=start_date, end=end_date_str,
                                   progress=False, auto_adjust=False, actions=True)
            df_soxl = yf.download("SOXL", start=start_date, end=end_date_str,
                                   progress=False, auto_adjust=False, actions=True)
            if df_qqq.empty or df_soxl.empty:
                time.sleep(1); continue
            is_multi_q = isinstance(df_qqq.columns, pd.MultiIndex)
            is_multi_s = isinstance(df_soxl.columns, pd.MultiIndex)
            qqq_close  = df_qqq['Close']['QQQ']   if is_multi_q else df_qqq['Close']
            soxl_close = df_soxl['Close']['SOXL'] if is_multi_s else df_soxl['Close']
            soxl_open  = df_soxl['Open']['SOXL']  if is_multi_s else df_soxl['Open']
            soxl_high  = df_soxl['High']['SOXL']  if is_multi_s else df_soxl['High']
            soxl_low   = df_soxl['Low']['SOXL']   if is_multi_s else df_soxl['Low']
            # ★ v9.8: dividend 컬럼 추출 (ex-div 날짜의 주당 배당금, 그 외 0)
            try:
                soxl_div = df_soxl['Dividends']['SOXL'] if is_multi_s else df_soxl['Dividends']
                soxl_div = soxl_div.fillna(0).astype(float)
            except (KeyError, AttributeError):
                soxl_div = pd.Series(0.0, index=soxl_close.index)
            df = pd.DataFrame({
                'QQQ': qqq_close,
                'SOXL': soxl_close,
                'SOXL_O': soxl_open,
                'SOXL_H': soxl_high,
                'SOXL_L': soxl_low,
                'SOXL_Div': soxl_div,
            })
            df['SOXL_Div'] = df['SOXL_Div'].fillna(0)
            df = df.sort_index().dropna(subset=['QQQ','SOXL'])
            df['QQQ']    = df['QQQ'].ffill().bfill()
            df['SOXL']   = df['SOXL'].ffill().bfill()
            df['SOXL_O'] = df['SOXL_O'].ffill().bfill()
            df['SOXL_H'] = df['SOXL_H'].ffill().bfill()
            df['SOXL_L'] = df['SOXL_L'].ffill().bfill()
            df.index = df.index.tz_localize(None)
            return df
        except Exception:
            time.sleep(1)
    return None
def calc_mode_series(df_qqq):
    if df_qqq is None:
        return None, None
    last_friday = df_qqq.index[df_qqq.index.dayofweek == 4].max()
    df_qqq_clean = df_qqq[df_qqq.index <= last_friday]
    qqq_weekly = df_qqq_clean.resample('W-FRI').last()
    delta      = qqq_weekly.diff()
    up         = delta.clip(lower=0)
    down       = -1 * delta.clip(upper=0)
    ema_up     = up.ewm(com=13, adjust=False).mean()
    ema_down   = down.ewm(com=13, adjust=False).mean()
    rs         = ema_up / ema_down
    rsi_series = 100 - (100 / (1 + rs))
    modes        = []
    current_mode = 'Safe'
    for i in range(len(rsi_series)):
        if i < 2:
            modes.append(current_mode); continue
        rsi_t1 = rsi_series.iloc[i - 1]
        rsi_t2 = rsi_series.iloc[i - 2]
        if np.isnan(rsi_t1) or np.isnan(rsi_t2):
            modes.append(current_mode); continue
        safe    = ((rsi_t2 > 65) and (rsi_t2 > rsi_t1)) or \
                  ((40 < rsi_t2 < 50) and (rsi_t2 > rsi_t1)) or \
                  ((rsi_t1 < 50) and (rsi_t2 > 50))
        offense = ((rsi_t2 < 35) and (rsi_t2 < rsi_t1)) or \
                  ((50 < rsi_t2 < 60) and (rsi_t2 < rsi_t1)) or \
                  ((rsi_t1 > 50) and (rsi_t2 < 50))
        if safe:    current_mode = 'Safe'
        elif offense: current_mode = 'Offense'
        modes.append(current_mode)
    weekly_mode = pd.Series(modes, index=qqq_weekly.index)
    daily_mode = weekly_mode.resample('D').ffill()
    daily_rsi  = rsi_series.resample('D').ffill()
    full_end = df_qqq.index[-1]
    if daily_mode.index[-1] < full_end:
        full_idx  = pd.date_range(daily_mode.index[0], full_end, freq='D')
        daily_mode = daily_mode.reindex(full_idx).ffill()
        daily_rsi  = daily_rsi.reindex(full_idx).ffill()
    return daily_mode, daily_rsi
def calc_qs_strength(df, window=QS_MA_WINDOW):
    ratio = df['SOXL'] / df['QQQ']
    ma    = ratio.rolling(window).mean()
    return (ratio / ma).rename('QS')
def qs_label_and_mul(qs_val):
    if qs_val < QS_LOW_THRESH:
        return f"🔥 과매도 ({qs_val:.3f}) → 슬롯 ×{QS_LOW_MULT}", QS_LOW_MULT, "qs-low"
    elif qs_val > QS_HIGH_THRESH:
        return f"❄️ 과매수 ({qs_val:.3f}) → 슬롯 ×{QS_HIGH_MULT}", QS_HIGH_MULT, "qs-high"
    else:
        return f"✅ 중립 ({qs_val:.3f}) → 슬롯 ×1.0", 1.0, "qs-mid"
# ★ v10.0: 카마릴라 R4 신호 계산 (bot.py(v10.0)/dongpa_camarilla_combo.py와 동일 로직, SOXL 고정)
def compute_camarilla_signal(df, coef=0.70, vol_filter_pct=0.80):
    """df는 SOXL_O/SOXL_H/SOXL_L/SOXL 컬럼 필요.
    resistance[d] = 전일 종가 + 전일 고저폭 * 1.1 * coef
    signal[d]     = 오늘 고가가 저항선 이상 (+ 변동성 백분위 필터 통과)
    entry_raw[d]  = 오늘 시가가 저항선 이상이면 시가, 아니면 저항선가
    nextO[d]      = 다음날 시가 (포지션 청산가). 마지막 행은 NaN."""
    O = df['SOXL_O'].values
    H = df['SOXL_H'].values
    L = df['SOXL_L'].values
    C = df['SOXL'].values
    n = len(df)
    prevH = np.roll(H, 1); prevH[0] = np.nan
    prevL = np.roll(L, 1); prevL[0] = np.nan
    prevC = np.roll(C, 1); prevC[0] = np.nan
    resistance = prevC + (prevH - prevL) * 1.1 * coef
    ret_cc = np.zeros(n)
    ret_cc[1:] = C[1:] / C[:-1] - 1
    vol20 = pd.Series(ret_cc).rolling(20).std().values
    vol_shift = np.roll(vol20, 1); vol_shift[0] = np.nan
    vol_rank = pd.Series(vol_shift).expanding(min_periods=60).rank(pct=True).values
    nextO = np.roll(O, -1); nextO[-1] = np.nan
    signal = (H >= resistance) & ~np.isnan(resistance)
    if vol_filter_pct is not None:
        vol_ok = (vol_rank <= vol_filter_pct) & ~np.isnan(vol_rank)
        signal = signal & vol_ok
    entry_raw = np.where(O >= resistance, O, resistance)
    return pd.DataFrame({
        'signal': signal, 'entry_raw': entry_raw, 'nextO': nextO,
        'resistance': resistance, 'vol_rank': vol_rank,
    }, index=df.index)
def _camarilla_today_candidate(df, real_cash, overlay_fraction, coef, vol_filter_pct):
    """오늘(아직 데이터에 없는 다음날) 신규진입 후보가/변동성필터 통과여부를
    어제까지의 실제 OHLC만으로 계산한다 (look-ahead 없음)."""
    last_C = float(df['SOXL'].iloc[-1])
    last_H = float(df['SOXL_H'].iloc[-1])
    last_L = float(df['SOXL_L'].iloc[-1])
    resistance_today = last_C + (last_H - last_L) * 1.1 * coef
    ret_cc = df['SOXL'].pct_change()
    vol20 = ret_cc.rolling(20).std()
    vol_shift = vol20.shift(1)
    extended = pd.concat([vol_shift, pd.Series([vol20.iloc[-1]])], ignore_index=True)
    vol_rank_today = extended.expanding(min_periods=60).rank(pct=True).iloc[-1]
    if vol_filter_pct is None:
        vol_ok = True
    else:
        vol_ok = (not np.isnan(vol_rank_today)) and (vol_rank_today <= vol_filter_pct)
    out = {
        'resistance': resistance_today, 'vol_rank': vol_rank_today,
        'filtered_out': not vol_ok, 'has_candidate': False,
    }
    if vol_ok and resistance_today > 0 and not np.isnan(resistance_today):
        out['has_candidate'] = True
        out['invest_amt_candidate'] = overlay_fraction * max(0.0, real_cash)
    return out
def get_overlay_cfg(settings):
    """settings(dict)에서 카마릴라 오버레이 설정을 OVERLAY_DEFAULTS로 보완해 반환."""
    cfg = dict(OVERLAY_DEFAULTS)
    for k in OVERLAY_DEFAULTS:
        if k in settings:
            cfg[k] = settings[k]
    cfg['overlay_enabled']  = bool(cfg['overlay_enabled'])
    cfg['overlay_fraction'] = float(cfg['overlay_fraction'])
    cfg['cama_coef']        = float(cfg['cama_coef'])
    vf = cfg['cama_vol_filter_pct']
    cfg['cama_vol_filter_pct'] = None if vf in (None, "null", "") else float(vf)
    cfg['cama_fee_rate']     = float(cfg['cama_fee_rate'])
    cfg['cama_slippage_pct'] = float(cfg['cama_slippage_pct'])
    return cfg
def get_repo():
    g = Github(GH_TOKEN)
    try:    return g.get_repo(REPO_KEY)
    except: return None
def load_settings():
    """★ v10.0: OVERLAY_DEFAULTS를 기본값으로 깔고 저장된 settings.json 값으로 덮어씀.
    새 키가 없으면 OVERLAY_DEFAULTS(overlay_enabled=False 등)가 그대로 쓰이므로 하위호환."""
    defaults = {"start_date": "2026-01-23", "init_cap": 100000.0}
    defaults.update(OVERLAY_DEFAULTS)
    try:
        repo = get_repo()
        if repo:
            contents = repo.get_contents(SETTINGS_FILE)
            saved = json.loads(contents.decoded_content.decode("utf-8"))
            merged = dict(defaults)
            merged.update(saved)
            return merged
    except: pass
    return defaults
def save_settings(settings_dict):
    try:
        repo     = get_repo()
        json_str = json.dumps(settings_dict)
        if repo:
            try:
                contents = repo.get_contents(SETTINGS_FILE)
                repo.update_file(contents.path, "Update settings", json_str, contents.sha)
            except:
                repo.create_file(SETTINGS_FILE, "Create settings", json_str)
    except Exception as e:
        print(f"설정 저장 실패: {e}")
def load_csv(filename, columns):
    try:
        repo = get_repo()
        if repo:
            try:
                contents   = repo.get_contents(filename)
                csv_string = contents.decoded_content.decode("utf-8")
                return pd.read_csv(StringIO(csv_string))
            except: pass
    except: pass
    return pd.DataFrame(columns=columns)
def save_csv(df, filename):
    try:
        repo       = get_repo()
        csv_string = df.to_csv(index=False)
        if repo:
            try:
                contents = repo.get_contents(filename)
                repo.update_file(contents.path, f"Update {filename}", csv_string, contents.sha)
            except:
                repo.create_file(filename, f"Create {filename}", csv_string)
    except Exception as e:
        st.error(f"GitHub 저장 실패: {e}")
@st.cache_resource
def get_gspread_workbook():
    try:
        creds_raw = st.secrets["general"]["GCP_CREDENTIALS"]
    except Exception:
        return None
    try:
        creds_dict = json.loads(creds_raw) if isinstance(creds_raw, str) else dict(creds_raw)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds  = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        print(f"⚠️ gspread 워크북 오픈 실패: {e}")
        return None
def load_tax_withdrawals():
    cols = ["날짜", "금액", "메모"]
    wb = get_gspread_workbook()
    if wb is None:
        return pd.DataFrame(columns=cols)
    try:
        ws = wb.worksheet(WITHDRAWAL_SHEET_NAME)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=cols)
    except Exception as e:
        print(f"⚠️ 인출 기록 탭 접근 실패: {e}")
        return pd.DataFrame(columns=cols)
    try:
        rows = ws.get_all_records()
    except Exception:
        return pd.DataFrame(columns=cols)
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    rename_map = {}
    for c in df.columns:
        key = str(c).strip()
        if key in ("날짜", "Date", "date"):        rename_map[c] = "날짜"
        elif key in ("금액", "Amount", "amount", "USD"): rename_map[c] = "금액"
        elif key in ("메모", "Memo", "memo", "Note"):    rename_map[c] = "메모"
    df = df.rename(columns=rename_map)
    for c in cols:
        if c not in df.columns:
            df[c] = "" if c == "메모" else None
    df["날짜"] = pd.to_datetime(df["날짜"], errors='coerce')
    df["금액"] = pd.to_numeric(df["금액"], errors='coerce').fillna(0.0)
    df = df.dropna(subset=["날짜"])
    df = df[df["금액"] > 0].copy()
    df = df.sort_values("날짜").reset_index(drop=True)
    return df[cols]
def save_tax_withdrawals(df):
    wb = get_gspread_workbook()
    if wb is None:
        st.error("⚠️ Streamlit Secrets 의 GCP_CREDENTIALS 가 설정되지 않아 시트에 저장할 수 없습니다.")
        return False
    try:
        ws = wb.worksheet(WITHDRAWAL_SHEET_NAME)
    except gspread.WorksheetNotFound:
        try:
            ws = wb.add_worksheet(title=WITHDRAWAL_SHEET_NAME, rows=200, cols=5)
        except Exception as e:
            st.error(f"⚠️ TaxWithdrawals 탭 생성 실패: {e}")
            return False
    header = ["날짜", "금액", "메모"]
    rows = [header]
    df_clean = df.copy() if df is not None else pd.DataFrame(columns=header)
    if not df_clean.empty:
        df_clean = df_clean.dropna(subset=["날짜"]) if "날짜" in df_clean.columns else df_clean
        if "날짜" in df_clean.columns:
            df_clean = df_clean.sort_values("날짜").reset_index(drop=True)
        for _, row in df_clean.iterrows():
            try:
                date_val = pd.to_datetime(row.get("날짜"))
                if pd.isna(date_val):
                    continue
                date_str = date_val.strftime("%Y-%m-%d")
            except Exception:
                continue
            try:
                amt = float(row.get("금액") or 0)
            except Exception:
                continue
            if amt <= 0:
                continue
            memo = str(row.get("메모", "") or "")
            rows.append([date_str, round(amt, 2), memo])
    try:
        ws.clear()
        ws.update(rows)
        return True
    except Exception as e:
        st.error(f"⚠️ 시트 쓰기 실패: {e}")
        return False
def auto_sync_engine(df, start_date, init_cap, withdrawals_df=None, overlay_cfg=None):
    """★ v10.0: overlay_cfg가 주어지고 overlay_enabled=True면 bot.py(v10.0)의
    get_orders_and_status와 동일한 3단계 루프(①전날 카마릴라 청산 → ②동파법 본 로직
    → ③신규 진입)를 수행한다. overlay_cfg가 None이거나 overlay_enabled=False면
    ①③ 스텝이 완전히 스킵되어 v9.8과 100% 동일한 결과를 낸다."""
    if overlay_cfg is None:
        overlay_cfg = dict(OVERLAY_DEFAULTS)
    overlay_enabled    = overlay_cfg['overlay_enabled']
    overlay_fraction   = overlay_cfg['overlay_fraction']
    cama_coef          = overlay_cfg['cama_coef']
    cama_vol_filter     = overlay_cfg['cama_vol_filter_pct']
    cama_fee_rate       = overlay_cfg['cama_fee_rate']
    cama_slippage_pct   = overlay_cfg['cama_slippage_pct']
    empty_cama = {'enabled': overlay_enabled}
    if df is None:
        return None, None, None, None, None, 1, 1.0, 1.0, 1.0, [], 0.0, 0.0, empty_cama
    mode_daily, _ = calc_mode_series(df['QQQ'])
    qs_daily      = calc_qs_strength(df)
    # ★ v9.8: 'Div' 컬럼 포함 / ★ v10.0: 오버레이 활성 시 카마릴라 신호 컬럼 추가
    concat_list = [df['SOXL'], df['SOXL_Div'], mode_daily, qs_daily]
    col_names   = ['Price', 'Div', 'Mode', 'QS']
    if overlay_enabled:
        cama_sig = compute_camarilla_signal(df, coef=cama_coef, vol_filter_pct=cama_vol_filter)
        concat_list += [cama_sig['signal'], cama_sig['entry_raw'], cama_sig['nextO']]
        col_names   += ['CamaSignal', 'CamaEntryRaw', 'CamaNextO']
    sim_df = pd.concat(concat_list, axis=1)
    sim_df.columns = col_names
    sim_df['Div'] = sim_df['Div'].fillna(0)
    sim_df = sim_df.dropna(subset=['Price','Mode','QS'])
    end_date = get_now_kst() - timedelta(days=1)
    mask   = (sim_df.index >= pd.to_datetime(start_date)) & \
             (sim_df.index <= pd.to_datetime(end_date.date()))
    sim_df = sim_df[mask]
    if sim_df.empty:
        return None, None, None, None, None, 1, 1.0, 1.0, 1.0, [], 0.0, 0.0, empty_cama
    sim_df['Prev_Price'] = sim_df['Price'].shift(1)
    sim_df['Prev_QS']    = sim_df['QS'].shift(1)
    sim_df['Prev_Mode']  = sim_df['Mode'].shift(1)
    sim_df = sim_df.dropna(subset=['Prev_Price','Prev_QS','Prev_Mode'])
    if withdrawals_df is None or withdrawals_df.empty:
        wd_queue = []
    else:
        wd_queue = [
            (pd.Timestamp(r["날짜"]).normalize(), float(r["금액"]), str(r.get("메모", "") or ""))
            for _, r in withdrawals_df.iterrows()
        ]
    wd_idx        = 0
    cum_withdrawn = 0.0
    cum_dividends = 0.0   # ★ v9.8
    real_cash        = init_cap
    cum_profit       = 0.0
    cum_loss         = 0.0
    slots            = []
    journal          = []
    daily_equity     = []
    full_action_log  = []
    cycle_days       = 0
    slot_sizes = {
        'Safe':    init_cap / MAX_SLOTS_SAFE,
        'Offense': init_cap / MAX_SLOTS_OFFENSE,
    }
    recent_slot_outcomes = []
    # ★ v10.0: 카마릴라 오버레이 상태 (동파법과 real_cash 공유)
    cama_position    = None   # {'invest_amt','entry_date','entry_raw','next_open'}
    cama_trade_count = 0
    cama_win_count   = 0
    cama_total_pnl   = 0.0
    for date, row in sim_df.iterrows():
        price      = row['Price']
        div_amt    = float(row.get('Div', 0) or 0)
        mode       = row['Mode']
        prev_price = row['Prev_Price']
        prev_qs    = row['Prev_QS']
        prev_mode  = row['Prev_Mode']
        # ★ v10.0 ① 전날 열어둔 카마릴라 포지션을 '오늘 시가'로 청산
        if overlay_enabled and cama_position is not None and not np.isnan(cama_position['next_open']):
            exit_eff  = cama_position['next_open'] * (1 - cama_slippage_pct)
            entry_eff = cama_position['entry_raw'] * (1 + cama_slippage_pct)
            trade_ret = (exit_eff / entry_eff - 1) - cama_fee_rate * 2 if entry_eff > 0 else 0.0
            proceeds  = cama_position['invest_amt'] * (1 + trade_ret)
            pnl       = proceeds - cama_position['invest_amt']
            real_cash      += proceeds
            cama_total_pnl += pnl
            cama_trade_count += 1
            if pnl > 0:
                cama_win_count += 1
            full_action_log.append({
                "날짜": date.date(), "구분": "🧨 카마릴라 청산",
                "가격": f"${cama_position['next_open']:.2f}", "수량": "-",
                "수익금": f"{'+' if pnl >= 0 else ''}${pnl:,.2f}",
                "비고": f"{cama_position['entry_date'].date()} 진입분 청산 (수익률 {trade_ret*100:+.2f}%)"
            })
            cama_position = None
        # ★ v9.8: 배당 cash 주입 (그날 매매 이전에 처리 — 전일 종료 시점 보유주식에 대해 지급)
        if div_amt > 0:
            held_shares = sum(s['shares'] for s in slots)
            if held_shares > 0:
                div_cash = div_amt * held_shares
                real_cash += div_cash
                cum_dividends += div_cash
                full_action_log.append({
                    "날짜": date.date(), "구분": "💰 배당 입금",
                    "가격": f"${div_amt:.4f}", "수량": held_shares,
                    "수익금": f"+${div_cash:,.2f}",
                    "비고": f"SOXL ex-div × {held_shares}주 (재투자용 cash 입금)"
                })
        ls_mul = loss_streak_mul(recent_slot_outcomes)
        sold_idx = []
        for i in range(len(slots) - 1, -1, -1):
            s    = slots[i]
            s['days'] += 1
            rule = LOCAL_PARAMS.get(s['birth_mode'], LOCAL_PARAMS['Safe'])
            if (price >= s['buy_price'] * rule['sell']) or (s['days'] >= rule['time']):
                rev  = s['shares'] * price
                prof = rev - (s['shares'] * s['buy_price'])
                current_holdings_val = sum(
                    slots[k]['shares'] * price for k in range(len(slots)) if k != i
                )
                equity_at_sell = real_cash + rev + current_holdings_val
                journal.append({
                    "날짜": date.date(), "총자산": equity_at_sell, "수익금": prof,
                    "수익률": (prof / (equity_at_sell - prof)) * 100 if (equity_at_sell - prof) > 0 else 0
                })
                full_action_log.append({
                    "날짜": date.date(), "구분": "매도 (Sell)", "가격": f"${price:.2f}",
                    "수량": s['shares'], "수익금": f"${prof:.2f}", "비고": "익절/기간만료"
                })
                real_cash += rev
                if prof > 0:
                    cum_profit += prof
                    recent_slot_outcomes.append(True)
                else:
                    cum_loss   += abs(prof)
                    recent_slot_outcomes.append(False)
                recent_slot_outcomes = recent_slot_outcomes[-LOSS_STREAK_WIN:]
                sold_idx.append(i)
        for i in sold_idx:
            del slots[i]
        curr_rule     = LOCAL_PARAMS.get(prev_mode, LOCAL_PARAMS['Safe'])
        _, qs_mul, _  = qs_label_and_mul(prev_qs)
        effective_max = QS_HIGH_SLOT_CAP if prev_qs > QS_HIGH_THRESH else max_slots_for(prev_mode)
        cur_slot_size = slot_sizes[prev_mode]
        loc_price     = prev_price * (1 + curr_rule['buy'])
        if price <= loc_price and len(slots) < effective_max:
            amt    = min(real_cash, cur_slot_size * qs_mul * ls_mul)
            shares = int(amt / loc_price)
            if shares > 0:
                invested = shares * price
                real_cash -= invested
                tr = PARAMS[prev_mode]
                tg = price * (1 + tr['sell'] / 100)
                cd = date + timedelta(days=tr['time'] * 1.45)
                slots.append({
                    '매수일': date.date(), '모드': prev_mode, '매수가': price, '수량': shares,
                    '목표가': tg, '손절기한': cd.date(),
                    'buy_price': price, 'shares': shares, 'days': 0, 'birth_mode': prev_mode
                })
                ls_note = f" LS×{ls_mul:.1f}" if ls_mul < 1.0 else ""
                full_action_log.append({
                    "날짜": date.date(), "구분": "매수 (Buy)", "가격": f"${price:.2f}",
                    "수량": shares, "수익금": "-",
                    "비고": f"{prev_mode} 진입 (QS×{qs_mul:.1f}{ls_note} @LOC${loc_price:.2f})"
                })
        # ★ v10.0 ③ 동파법이 쓰고 남은 real_cash 의 overlay_fraction 만큼 카마릴라 신규 진입
        if overlay_enabled and bool(row.get('CamaSignal', False)) and cama_position is None:
            entry_raw = row['CamaEntryRaw']
            next_open = row['CamaNextO']
            if entry_raw > 0 and not np.isnan(entry_raw):
                leftover   = max(0.0, real_cash)
                invest_amt = overlay_fraction * leftover
                if invest_amt > 1e-6:
                    real_cash -= invest_amt
                    cama_position = {'invest_amt': invest_amt, 'entry_date': date,
                                      'entry_raw': entry_raw, 'next_open': next_open}
                    full_action_log.append({
                        "날짜": date.date(), "구분": "🧨 카마릴라 진입",
                        "가격": f"${entry_raw:.2f}", "수량": "-",
                        "수익금": "-", "비고": f"신규 투입 ${invest_amt:,.0f}"
                    })
        while wd_idx < len(wd_queue) and wd_queue[wd_idx][0] <= pd.Timestamp(date).normalize():
            wd_amount      = wd_queue[wd_idx][1]
            wd_memo        = wd_queue[wd_idx][2]
            real_cash     -= wd_amount
            cum_withdrawn += wd_amount
            full_action_log.append({
                "날짜": date.date(), "구분": "💸 양도세 인출",
                "가격": "-", "수량": "-",
                "수익금": f"-${wd_amount:,.2f}",
                "비고": f"외부 출금{(' — ' + wd_memo) if wd_memo else ''}"
            })
            wd_idx += 1
        total_holdings_value = sum(s['shares'] * price for s in slots)
        cama_equity_today    = cama_position['invest_amt'] if cama_position is not None else 0.0
        daily_total_equity   = real_cash + total_holdings_value + cama_equity_today
        daily_equity.append({"날짜": date.date(), "총자산": daily_total_equity})
        cycle_days += 1
        if cycle_days >= RESET_CYCLE:
            # ★ v9.8: virtual 에서 인출 차감 (배당은 cash 에 이미 반영되어 reset 자본을 키움)
            # ★ v10.0: 카마릴라 손익은 동파법 가상자본 계산에 포함하지 않음 (회계 분리)
            virtual = init_cap + (cum_profit * 0.7) - (cum_loss * 0.6) - cum_withdrawn + cum_dividends * 0.7
            if virtual < 1000: virtual = 1000
            slot_sizes['Safe']    = virtual / MAX_SLOTS_SAFE
            slot_sizes['Offense'] = virtual / MAX_SLOTS_OFFENSE
            cycle_days = 0
    final_holdings = [
        {"매수일": s['매수일'], "모드": s['모드'], "매수가": s['매수가'],
         "수량": s['수량'], "목표가": s['목표가'], "손절기한": s['손절기한']}
        for s in slots
    ]
    df_actions = pd.DataFrame(full_action_log)
    if not df_actions.empty:
        df_actions = df_actions.sort_values(by="날짜", ascending=False).reset_index(drop=True)
    today_qs_val = float(qs_daily.iloc[-1]) if not qs_daily.empty else 1.0
    _, today_qs_mul, _ = qs_label_and_mul(today_qs_val)
    today_ls_mul = loss_streak_mul(recent_slot_outcomes)
    today_mode = mode_daily.iloc[-1] if not mode_daily.empty else 'Safe'
    today_slot_size = slot_sizes.get(today_mode, slot_sizes['Safe'])
    # ★ v10.0: 카마릴라 "오늘" 상태 (청산 대상 포지션이 있는지 / 신규진입 후보가 있는지)
    cama_equity = cama_position['invest_amt'] if cama_position is not None else 0.0
    cama_info = {'enabled': overlay_enabled}
    if overlay_enabled:
        cama_info['trade_count'] = cama_trade_count
        cama_info['win_rate']    = (cama_win_count / cama_trade_count) if cama_trade_count > 0 else None
        cama_info['total_pnl']   = cama_total_pnl
        cama_info['equity']      = cama_equity
        today = {'has_open_position': False, 'has_candidate': False}
        if cama_position is not None:
            today['has_open_position'] = True
            today['entry_date']  = cama_position['entry_date'].date()
            today['entry_raw']   = cama_position['entry_raw']
            today['invest_amt']  = cama_position['invest_amt']
        else:
            today.update(_camarilla_today_candidate(
                df, real_cash, overlay_fraction, cama_coef, cama_vol_filter))
        cama_info['today'] = today
    return (
        pd.DataFrame(final_holdings),
        pd.DataFrame(journal),
        pd.DataFrame(daily_equity),
        df_actions,
        today_slot_size,
        cycle_days,
        today_qs_val,
        today_qs_mul,
        today_ls_mul,
        recent_slot_outcomes[-LOSS_STREAK_WIN:],
        cum_withdrawn,
        cum_dividends,   # ★ v9.8: 누적 배당 추가
        cama_info,        # ★ v10.0: 카마릴라 오버레이 상태
    )


def run_backtest_fixed(df, start_date, end_date, init_cap,
                        include_fees=False, include_tax=False,
                        buy_fee_rate=0.00015, sell_fee_rate=0.0001706,
                        tax_deduction_usd=1786.0, tax_rate=0.22,
                        tax_strategy='A', custom_schedule=None,
                        overlay_enabled=False, overlay_fraction=0.70,
                        cama_coef=0.70, cama_vol_filter_pct=0.80,
                        cama_fee_rate=0.0005, cama_slippage_pct=0.0010):
    """★ v9.8 원본 로직(세금/수수료 트랜치 스케줄링 포함) + ★ v10.0 카마릴라
    오버레이(①전날 청산 → ②동파법 본 로직 → ③신규 진입, real_cash 공유).
    overlay_enabled=False(기본값)면 ①③ 스텝이 완전히 스킵되어 v9.8과 100% 동일한
    결과를 낸다 — 세금/수수료 트랜치 로직은 이 옵션과 무관하게 원본 그대로 동작한다."""
    TAX_SCHEDULES = {'A': [(1.00, (5, 1), (5, 31))]}
    if tax_strategy == 'B' and custom_schedule is not None and len(custom_schedule) > 0:
        tax_tranches_def = custom_schedule
    else:
        tax_tranches_def = TAX_SCHEDULES.get('A', [(1.00, (5, 1), (5, 31))])
    if df is None:
        return None, None, None, None, None

    mode_daily, rsi_daily = calc_mode_series(df['QQQ'])
    qs_daily = calc_qs_strength(df)
    concat_list = [df['SOXL'], df['SOXL_Div'], mode_daily, rsi_daily, qs_daily]
    col_names   = ['Price', 'Div', 'Mode', 'RSI', 'QS']
    if overlay_enabled:
        cama_sig = compute_camarilla_signal(df, coef=cama_coef, vol_filter_pct=cama_vol_filter_pct)
        concat_list += [cama_sig['signal'], cama_sig['entry_raw'], cama_sig['nextO']]
        col_names   += ['CamaSignal', 'CamaEntryRaw', 'CamaNextO']

    sim_df = pd.concat(concat_list, axis=1)
    sim_df.columns = col_names
    sim_df['Div'] = sim_df['Div'].fillna(0)
    sim_df = sim_df.dropna(subset=['Price', 'Mode', 'QS'])
    mask = (sim_df.index >= pd.to_datetime(start_date)) & (sim_df.index <= pd.to_datetime(end_date))
    sim_df = sim_df[mask]
    if sim_df.empty:
        return None, None, None, None, None
    sim_df['Prev_Price'] = sim_df['Price'].shift(1)
    sim_df['Prev_QS'] = sim_df['QS'].shift(1)
    sim_df['Prev_Mode'] = sim_df['Mode'].shift(1)
    sim_df = sim_df.dropna(subset=['Prev_Price', 'Prev_QS', 'Prev_Mode'])

    real_cash = init_cap
    cum_profit = 0.0
    cum_loss = 0.0
    cum_dividends = 0.0
    slots = []
    equity_curve = []
    debug_logs = []
    cama_log = []
    gross_profit = 0.0
    gross_loss = 0.0
    cycle_days = 0
    slot_sizes = {'Safe': init_cap / MAX_SLOTS_SAFE, 'Offense': init_cap / MAX_SLOTS_OFFENSE}
    recent_slot_outcomes = []
    total_buy_fees = 0.0
    total_sell_fees = 0.0
    annual_realized = 0.0
    annual_realized_tax = 0.0
    total_tax_paid = 0.0
    last_year_seen = None
    tax_log = []
    yearly_realized_log = {}
    yearly_fee_log = {}
    yearly_tax_log = {}
    yearly_div_log = {}
    yearly_cama_pnl_log = {}
    pending_tranches = []
    forced_count = 0
    negative_cash_days = 0

    # ★ v10.0: 카마릴라 오버레이 상태 (동파법과 real_cash 공유)
    cama_position = None     # {'invest_amt', 'entry_date', 'entry_raw', 'next_open'}
    cama_trade_count = 0
    cama_win_count = 0
    cama_total_pnl = 0.0

    for date, row in sim_df.iterrows():
        price = row['Price']
        div_amt = float(row.get('Div', 0) or 0)
        prev_price = row['Prev_Price']
        prev_qs = row['Prev_QS']
        prev_mode = row['Prev_Mode']
        cur_year = date.year

        # ── ① v10.0: 전날 열어둔 카마릴라 포지션을 '오늘 시가'로 청산 ──────
        if overlay_enabled and cama_position is not None:
            exit_eff = cama_position['next_open'] * (1 - cama_slippage_pct)
            entry_eff = cama_position['entry_raw'] * (1 + cama_slippage_pct)
            if not np.isnan(exit_eff) and entry_eff > 0:
                gross_ret = exit_eff / entry_eff - 1
                trade_ret = gross_ret - cama_fee_rate * 2
            else:
                trade_ret = 0.0
            proceeds = cama_position['invest_amt'] * (1 + trade_ret)
            pnl = proceeds - cama_position['invest_amt']
            real_cash += proceeds
            cama_total_pnl += pnl
            yearly_cama_pnl_log[cur_year] = yearly_cama_pnl_log.get(cur_year, 0.0) + pnl
            cama_trade_count += 1
            if pnl > 0:
                cama_win_count += 1
            cama_log.append({
                "진입일": cama_position['entry_date'].date(), "청산일": date.date(),
                "투입금": f"${cama_position['invest_amt']:,.0f}",
                "수익률": f"{trade_ret*100:+.2f}%", "손익": f"${pnl:,.2f}",
            })
            cama_position = None

        # ── ② v9.8 원본: 배당 cash 주입 + 연도 롤오버 세금 트랜치 스케줄링 ──
        if div_amt > 0:
            held_shares = sum(s['shares'] for s in slots)
            if held_shares > 0:
                div_cash = div_amt * held_shares
                real_cash += div_cash
                cum_dividends += div_cash
                yearly_div_log[cur_year] = yearly_div_log.get(cur_year, 0.0) + div_cash

        if last_year_seen is not None and cur_year != last_year_seen:
            yearly_realized_log[last_year_seen] = annual_realized
            if include_tax:
                annual_tax = max(0.0, annual_realized_tax - tax_deduction_usd) * tax_rate
                if annual_tax > 0:
                    for entry in tax_tranches_def:
                        if len(entry) == 4:
                            frac, (em, ed), (fm, fd), yoff = entry
                        else:
                            frac, (em, ed), (fm, fd) = entry
                            yoff = 0
                        if yoff == -1:
                            tax_due = annual_tax * frac
                            actual = min(tax_due, max(0.0, real_cash))
                            if actual > 0:
                                real_cash -= actual
                                total_tax_paid += actual
                                yearly_tax_log[last_year_seen] = yearly_tax_log.get(last_year_seen, 0.0) + actual
                                tax_log.append((date, actual, 'dec_anticipated'))
                            remaining = tax_due - actual
                            if remaining > 1e-6:
                                pending_tranches.append({
                                    'amount': remaining,
                                    'earliest': pd.Timestamp(year=cur_year, month=1, day=1),
                                    'force': pd.Timestamp(year=cur_year, month=1, day=31),
                                    'paid': 0.0, 'year': last_year_seen,
                                })
                        else:
                            pending_tranches.append({
                                'amount': annual_tax * frac,
                                'earliest': pd.Timestamp(year=cur_year, month=em, day=ed),
                                'force': pd.Timestamp(year=cur_year, month=fm, day=fd),
                                'paid': 0.0, 'year': last_year_seen,
                            })
            annual_realized = 0.0
            annual_realized_tax = 0.0
        last_year_seen = cur_year

        ls_mul = loss_streak_mul(recent_slot_outcomes)
        if prev_qs < QS_LOW_THRESH:
            qs_label = 'Low'
        elif prev_qs > QS_HIGH_THRESH:
            qs_label = 'High'
        else:
            qs_label = 'Normal'
        ls_label = 'ON' if ls_mul < 1.0 else '-'
        _, qs_mul, _ = qs_label_and_mul(prev_qs)
        effective_max = QS_HIGH_SLOT_CAP if prev_qs > QS_HIGH_THRESH else max_slots_for(prev_mode)

        sold_idx = []
        sold_qty_total = 0
        sold_pnl_total = 0.0
        for i in range(len(slots) - 1, -1, -1):
            s = slots[i]
            s['days'] += 1
            rule = LOCAL_PARAMS.get(s['birth_mode'], LOCAL_PARAMS['Safe'])
            if (price >= s['buy_price'] * rule['sell']) or (s['days'] >= rule['time']):
                gross_rev = s['shares'] * price
                sell_fee = gross_rev * sell_fee_rate if include_fees else 0.0
                net_rev = gross_rev - sell_fee
                cost_basis = s.get('cost_basis', s['shares'] * s['buy_price'])
                prof = net_rev - cost_basis
                real_cash += net_rev
                total_sell_fees += sell_fee
                yearly_fee_log[cur_year] = yearly_fee_log.get(cur_year, 0.0) + sell_fee
                annual_realized += prof
                if include_tax:
                    annual_realized_tax += prof
                if prof > 0:
                    cum_profit += prof; gross_profit += prof
                    recent_slot_outcomes.append(True)
                else:
                    cum_loss += abs(prof); gross_loss += abs(prof)
                    recent_slot_outcomes.append(False)
                recent_slot_outcomes = recent_slot_outcomes[-LOSS_STREAK_WIN:]
                sold_idx.append(i)
                sold_qty_total += s['shares']
                sold_pnl_total += prof
        for i in sold_idx:
            del slots[i]
        if sold_qty_total > 0:
            allocated_cap = sum(s['shares'] * price for s in slots)
            total_asset = real_cash + allocated_cap
            balance_qty = sum(s['shares'] for s in slots)
            debug_logs.append({
                "날짜": date.date(), "Action": "매도", "적용 모드": prev_mode,
                "QS신호": f"{qs_label} ({prev_qs:.3f})", "LS가드": ls_label, "최대슬롯": effective_max,
                "종가": f"${price:.2f}", "수량": f"{-sold_qty_total:+,d}",
                "실현손익": f"${sold_pnl_total:,.2f}", "Balance_Qty": f"{balance_qty:,d}",
                "Total_Cash": f"${real_cash:,.0f}", "Allocated_Cap": f"${allocated_cap:,.0f}",
                "Total_Asset": f"${total_asset:,.0f}", "Return_Pct": f"{(total_asset/init_cap-1)*100:+.2f}%",
            })

        curr_rule = LOCAL_PARAMS.get(prev_mode, LOCAL_PARAMS['Safe'])
        cur_slot_size = slot_sizes[prev_mode]
        loc_price = prev_price * (1 + curr_rule['buy'])
        if price <= loc_price and len(slots) < effective_max:
            amt = min(real_cash, cur_slot_size * qs_mul * ls_mul)
            shares = int(amt / loc_price)
            if shares > 0:
                invested = shares * price
                buy_fee = invested * buy_fee_rate if include_fees else 0.0
                cost_basis = invested + buy_fee
                real_cash -= cost_basis
                total_buy_fees += buy_fee
                yearly_fee_log[cur_year] = yearly_fee_log.get(cur_year, 0.0) + buy_fee
                slots.append({'buy_price': price, 'shares': shares, 'days': 0,
                              'birth_mode': prev_mode, 'cost_basis': cost_basis})
                allocated_cap = sum(s['shares'] * price for s in slots)
                total_asset = real_cash + allocated_cap
                balance_qty = sum(s['shares'] for s in slots)
                debug_logs.append({
                    "날짜": date.date(), "Action": "매수", "적용 모드": prev_mode,
                    "QS신호": f"{qs_label} ({prev_qs:.3f})", "LS가드": ls_label, "최대슬롯": effective_max,
                    "종가": f"${price:.2f}", "수량": f"+{shares:,d}",
                    "실현손익": "$0.00", "Balance_Qty": f"{balance_qty:,d}",
                    "Total_Cash": f"${real_cash:,.0f}", "Allocated_Cap": f"${allocated_cap:,.0f}",
                    "Total_Asset": f"${total_asset:,.0f}", "Return_Pct": f"{(total_asset/init_cap-1)*100:+.2f}%",
                })

        # ── ③ v10.0: 동파법이 쓰고 남은 현금의 overlay_fraction 만큼 카마릴라 신규 진입 ──
        if overlay_enabled and bool(row.get('CamaSignal', False)) and cama_position is None:
            next_open = row['CamaNextO']
            entry_raw = row['CamaEntryRaw']
            if not np.isnan(next_open) and entry_raw > 0:
                leftover = max(0.0, real_cash)
                invest_amt = overlay_fraction * leftover
                if invest_amt > 1e-6:
                    real_cash -= invest_amt
                    cama_position = {
                        'invest_amt': invest_amt, 'entry_date': date,
                        'entry_raw': entry_raw, 'next_open': next_open,
                    }

        dongpa_equity = sum(s['shares'] * price for s in slots)
        cama_equity = cama_position['invest_amt'] if cama_position is not None else 0.0
        current_equity = real_cash + dongpa_equity + cama_equity
        equity_curve.append({'Date': date, 'Equity': current_equity})

        cycle_days += 1
        is_cycle_end = (cycle_days >= RESET_CYCLE)
        for tranche in pending_tranches:
            remaining = tranche['amount'] - tranche['paid']
            if remaining <= 1e-6:
                continue
            past_earliest = date >= tranche['earliest']
            past_force = date >= tranche['force']
            if past_force:
                actual = remaining
                real_cash -= actual
                total_tax_paid += actual
                tranche['paid'] += actual
                yearly_tax_log[cur_year] = yearly_tax_log.get(cur_year, 0.0) + actual
                tax_log.append((date, actual, 'force'))
                forced_count += 1
            elif past_earliest and is_cycle_end:
                actual = min(remaining, max(0.0, real_cash))
                if actual > 0:
                    real_cash -= actual
                    total_tax_paid += actual
                    tranche['paid'] += actual
                    yearly_tax_log[cur_year] = yearly_tax_log.get(cur_year, 0.0) + actual
                    tax_log.append((date, actual, 'cycle'))
        pending_tranches = [t for t in pending_tranches if (t['amount'] - t['paid']) > 1e-6]
        if real_cash < 0:
            negative_cash_days += 1
        if is_cycle_end:
            # ★ v9.8/v10.0: 카마릴라 손익은 가상자본 계산에 포함하지 않음 (회계 분리)
            virtual = init_cap + (cum_profit * 0.7) - (cum_loss * 0.6) - total_tax_paid + cum_dividends * 0.7
            if virtual < 1000:
                virtual = 1000
            slot_sizes['Safe'] = virtual / MAX_SLOTS_SAFE
            slot_sizes['Offense'] = virtual / MAX_SLOTS_OFFENSE
            cycle_days = 0

    if last_year_seen is not None:
        yearly_realized_log.setdefault(last_year_seen, annual_realized)
    pending_unrealized = (max(0.0, annual_realized_tax - tax_deduction_usd) * tax_rate
                          if include_tax else 0.0)
    pending_unfunded = sum(t['amount'] - t['paid'] for t in pending_tranches)
    pending_tax_at_end = pending_unrealized + pending_unfunded

    res_df = pd.DataFrame(equity_curve).set_index('Date')
    df_debug = pd.DataFrame(debug_logs).reset_index(drop=True) if debug_logs else pd.DataFrame()
    df_cama = pd.DataFrame(cama_log) if cama_log else pd.DataFrame()

    if not res_df.empty:
        res_df['Returns'] = res_df['Equity'].pct_change()
        downside_returns = res_df.loc[res_df['Returns'] < 0, 'Returns']
        downside_std = downside_returns.std() * np.sqrt(252)
        total_ret = (res_df['Equity'].iloc[-1] / init_cap) - 1
        days = (res_df.index[-1] - res_df.index[0]).days
        cagr = (1 + total_ret) ** (365 / days) - 1 if days > 0 else 0
        sortino = cagr / downside_std if downside_std > 0 else 0
        peak = res_df['Equity'].cummax()
        mdd = ((res_df['Equity'] - peak) / peak).min()
        metrics = {
            'cagr': cagr, 'mdd': mdd, 'calmar': (cagr / abs(mdd)) if mdd < 0 else np.nan,
            'final_equity': res_df['Equity'].iloc[-1],
            'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 99.9,
            'sortino': sortino,
            'total_buy_fees': total_buy_fees, 'total_sell_fees': total_sell_fees,
            'total_fees': total_buy_fees + total_sell_fees,
            'total_tax_paid': total_tax_paid, 'tax_pending_end': pending_tax_at_end,
            'tax_log': tax_log, 'include_fees': include_fees, 'include_tax': include_tax,
            'tax_strategy': tax_strategy, 'forced_count': forced_count,
            'negative_cash_days': negative_cash_days, 'total_dividends': cum_dividends,
            # ★ v10.0: 카마릴라 오버레이 지표
            'cama_trade_count': cama_trade_count,
            'cama_win_rate': (cama_win_count / cama_trade_count) if cama_trade_count > 0 else np.nan,
            'cama_total_pnl': cama_total_pnl,
            'overlay_enabled': overlay_enabled, 'overlay_fraction': overlay_fraction,
        }
    else:
        metrics = {'cagr': np.nan, 'mdd': np.nan, 'calmar': np.nan, 'final_equity': np.nan}

    yearly_stats = []
    def calc_mdd(series):
        peak = series.cummax()
        return ((series - peak) / peak).min()
    prev_equity = init_cap
    for yr in res_df.index.year.unique():
        df_yr = res_df[res_df.index.year == yr]
        end_equity = df_yr['Equity'].iloc[-1]
        yr_return = (end_equity - prev_equity) / prev_equity
        yr_mdd = calc_mdd(df_yr['Equity'])
        yearly_stats.append({
            "연도": yr, "수익률": yr_return, "MDD": yr_mdd, "기말자산": end_equity,
            "수수료": yearly_fee_log.get(yr, 0.0), "양도세": yearly_tax_log.get(yr, 0.0),
            "실현손익": yearly_realized_log.get(yr, 0.0), "배당": yearly_div_log.get(yr, 0.0),
            "카마릴라손익": yearly_cama_pnl_log.get(yr, 0.0),  # ★ v10.0
        })
        prev_equity = end_equity

    return res_df, metrics, pd.DataFrame(yearly_stats).set_index("연도"), df_debug, df_cama


def main():
    st.title("💎 동파법 마스터 v10.0 (+카마릴라 오버레이)")
    tab_trade, tab_backtest, tab_logic = st.tabs(["💎 실전 트레이딩", "🧪 백테스트", "📚 전략 로직"])
    with st.spinner("데이터 로딩 중... (3회 재시도)"):
        df = get_data_final()
    offline_mode = df is None
    if offline_mode:
        st.warning("⚠️ **오프라인 모드:** 현재가 업데이트 중단.")
    if not offline_mode:
        mode_s, rsi_s  = calc_mode_series(df['QQQ'])
        qs_series      = calc_qs_strength(df)
        curr_mode      = mode_s.iloc[-1]
        curr_rsi       = rsi_s.iloc[-1]
        curr_qs_val    = float(qs_series.iloc[-1])
        qs_lbl, curr_qs_mul, qs_badge = qs_label_and_mul(curr_qs_val)
        soxl_price     = df['SOXL'].iloc[-1]
        prev_close     = df['SOXL'].iloc[-2]
    else:
        curr_mode   = 'Safe'
        curr_rsi    = 0.0
        curr_qs_val = 1.0
        curr_qs_mul = 1.0
        qs_lbl      = "오프라인"
        qs_badge    = "qs-mid"
        soxl_price  = 0.0
        prev_close  = 0.0
    settings = load_settings()
    if 'auto_run_done' not in st.session_state:
        st.session_state['auto_run_done'] = False
    try:
        saved_start_date = datetime.strptime(settings.get("start_date", "2026-01-23"), "%Y-%m-%d").date()
        saved_init_cap   = float(settings.get("init_cap", 100000.0))
    except:
        saved_start_date = datetime(2026, 1, 23).date()
        saved_init_cap   = 100000.0
    saved_overlay_cfg = get_overlay_cfg(settings)   # ★ v10.0
    withdrawals_df = load_tax_withdrawals()
    if not offline_mode and ('holdings' not in st.session_state or not st.session_state['auto_run_done']):
        result = auto_sync_engine(df, saved_start_date, saved_init_cap,
                                   withdrawals_df=withdrawals_df, overlay_cfg=saved_overlay_cfg)
        (h_auto, j_auto, eq_auto, log_auto, c_slot, c_day,
         c_qs, c_qs_mul, c_ls_mul, c_recent, c_wd, c_div, c_cama) = result
        if h_auto is not None:
            old_h = load_csv(HOLDINGS_FILE, h_auto.columns)
            if len(h_auto) != len(old_h) or (
                not old_h.empty and str(h_auto.iloc[-1].values) != str(old_h.iloc[-1].values)
            ):
                save_csv(h_auto,  HOLDINGS_FILE)
                save_csv(j_auto,  JOURNAL_FILE)
                save_csv(eq_auto, EQUITY_FILE)
            st.session_state['holdings']          = h_auto
            st.session_state['journal']           = j_auto
            st.session_state['equity_history']    = eq_auto
            st.session_state['action_log']        = log_auto
            st.session_state['current_slot_size'] = c_slot
            st.session_state['current_cycle']     = (c_day % RESET_CYCLE) + 1
            st.session_state['current_qs']        = c_qs
            st.session_state['current_qs_mul']    = c_qs_mul
            st.session_state['current_ls_mul']    = c_ls_mul
            st.session_state['recent_outcomes']   = c_recent
            st.session_state['cum_withdrawn']     = c_wd
            st.session_state['cum_dividends']     = c_div   # ★ v9.8
            st.session_state['cama_info']         = c_cama  # ★ v10.0
            st.session_state['auto_run_done']     = True
    if 'holdings'      not in st.session_state:
        st.session_state['holdings']      = load_csv(HOLDINGS_FILE, ["매수일","모드","매수가","수량","목표가","손절기한"])
    if 'journal'       not in st.session_state:
        st.session_state['journal']       = load_csv(JOURNAL_FILE,  ["날짜","총자산","수익금","수익률"])
    if 'equity_history' not in st.session_state:
        st.session_state['equity_history'] = load_csv(EQUITY_FILE, ["날짜","총자산"])
    if 'action_log'    not in st.session_state:
        st.session_state['action_log']    = pd.DataFrame()
    if 'cama_info'      not in st.session_state:   # ★ v10.0
        st.session_state['cama_info']     = {'enabled': saved_overlay_cfg['overlay_enabled']}
    curr_ls_mul  = st.session_state.get('current_ls_mul', 1.0)
    curr_recent  = st.session_state.get('recent_outcomes', [])
    curr_cum_wd  = st.session_state.get('cum_withdrawn', 0.0)
    curr_cum_div = st.session_state.get('cum_dividends', 0.0)   # ★ v9.8
    curr_cama    = st.session_state.get('cama_info', {'enabled': False})   # ★ v10.0
    with tab_trade:
        with st.sidebar:
            st.header("🤖 설정 및 초기화")
            auto_start_date = st.date_input("전략 시작일", value=saved_start_date)
            auto_init_cap   = st.number_input("시작 원금 ($)", value=saved_init_cap, step=100.0)
            st.markdown("---")
            st.markdown("#### 🧨 카마릴라 R4 오버레이")
            overlay_enabled = st.checkbox(
                "오버레이 사용 (다음 매매봇 실행에도 즉시 반영)", value=saved_overlay_cfg['overlay_enabled'])
            overlay_fraction = st.slider(
                "투입 비중 (남은 현금 중 %)", 0.0, 1.0, saved_overlay_cfg['overlay_fraction'], 0.05,
                disabled=not overlay_enabled)
            cama_coef = st.slider(
                "R4 저항선 계수 (coef)", 0.10, 2.00, saved_overlay_cfg['cama_coef'], 0.05,
                disabled=not overlay_enabled)
            use_vol_filter = st.checkbox(
                "변동성 백분위 필터 사용", value=saved_overlay_cfg['cama_vol_filter_pct'] is not None,
                disabled=not overlay_enabled)
            if use_vol_filter:
                vf_default = saved_overlay_cfg['cama_vol_filter_pct'] \
                    if saved_overlay_cfg['cama_vol_filter_pct'] is not None else 0.80
                cama_vol_filter_pct = st.slider(
                    "변동성 백분위 상한", 0.0, 1.0, vf_default, 0.05, disabled=not overlay_enabled)
            else:
                cama_vol_filter_pct = None
            cama_fee_rate = st.number_input(
                "왕복 수수료율 (편도)", value=saved_overlay_cfg['cama_fee_rate'], step=0.0001, format="%.4f",
                disabled=not overlay_enabled)
            cama_slippage_pct = st.number_input(
                "슬리피지율 (편도)", value=saved_overlay_cfg['cama_slippage_pct'], step=0.0001, format="%.4f",
                disabled=not overlay_enabled)
            st.caption("settings.json에 저장되어 다음 실제 매매 봇(bot.py v10.0) 실행 때도 그대로 반영됩니다.")
            st.markdown("---")
            if not offline_mode:
                if st.button("🔄 설정 변경 및 재동기화", type="primary"):
                    new_settings = {
                        "start_date": auto_start_date.strftime("%Y-%m-%d"),
                        "init_cap":   auto_init_cap,
                        "overlay_enabled":     overlay_enabled,
                        "overlay_fraction":    overlay_fraction,
                        "cama_coef":           cama_coef,
                        "cama_vol_filter_pct": cama_vol_filter_pct,
                        "cama_fee_rate":       cama_fee_rate,
                        "cama_slippage_pct":   cama_slippage_pct,
                    }
                    save_settings(new_settings)
                    st.session_state['auto_run_done'] = False
                    st.rerun()
            else:
                st.button("🚫 오프라인 (설정 변경 불가)", disabled=True)
            st.markdown("---")
            st.markdown("#### ⚙️ 현재 파라미터 (v9.8)")
            st.markdown(f"""
            | | 🛡️ Safe | ⚔️ Offense |
            |---|---|---|
            | 매수 타점 | <3% | <3% |
            | 익절 목표 | +0.5% | +4.0% |
            | 보유 기간 | 28일 | 7일 |
            """)
            st.markdown("#### 📡 QS_strength 신호")
            st.markdown(f"""
            | QS 구간 | 슬롯 배수 |
            |---|---|
            | QS < {QS_LOW_THRESH} (과매도) | **×{QS_LOW_MULT} ▲** |
            | {QS_LOW_THRESH} ~ {QS_HIGH_THRESH} (중립) | ×1.0 |
            | QS > {QS_HIGH_THRESH} (과매수) | **×{QS_HIGH_MULT} ▼** (최대 {QS_HIGH_SLOT_CAP}슬롯) |
            """)
            st.markdown("#### 🛡️ Loss Streak Guard")
            st.markdown(f"""
            | 조건 | 슬롯 배수 |
            |---|---|
            | 최근 **{LOSS_STREAK_N}건 연속 손실** | **×{LOSS_STREAK_MUL} ▼** |
            | 그 외 | ×1.0 |
            """)
            st.markdown("#### ⚖️ 모드별 분할")
            st.markdown(f"""
            | 모드 | 분할수 | 1슬롯 비중 |
            |---|---|---|
            | ⚔️ Offense | **{MAX_SLOTS_OFFENSE}** | ≈{100/MAX_SLOTS_OFFENSE:.1f}% |
            | 🛡️ Safe    | **{MAX_SLOTS_SAFE}** | ≈{100/MAX_SLOTS_SAFE:.1f}% |
            """)
            st.markdown(f"**복리 주기:** {RESET_CYCLE}일")
            st.markdown("---")
            if st.button("🗑️ 데이터 초기화"):
                empty_df = pd.DataFrame(columns=["매수일","모드","매수가","수량","목표가","손절기한"])
                empty_j  = pd.DataFrame(columns=["날짜","총자산","수익금","수익률"])
                empty_eq = pd.DataFrame(columns=["날짜","총자산"])
                save_csv(empty_df, HOLDINGS_FILE)
                save_csv(empty_j,  JOURNAL_FILE)
                save_csv(empty_eq, EQUITY_FILE)
                for key in ['holdings','journal','equity_history','action_log']:
                    st.session_state.pop(key, None)
                st.rerun()
            cycle = st.session_state.get('current_cycle', 1)
            st.info(f"🔄 사이클: **{cycle}일차** / {RESET_CYCLE}일")
        r       = PARAMS[curr_mode]
        slot_sz = st.session_state.get('current_slot_size',
                                        saved_init_cap / max_slots_for(curr_mode))
        adj_slot = slot_sz * curr_qs_mul * curr_ls_mul
        today   = get_now_kst().date()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("시장 모드",    f"{r['desc']}", f"RSI {curr_rsi:.2f}" if not offline_mode else "Offline", delta_color="inverse")
        c2.metric("SOXL 현재가", f"${soxl_price:.2f}" if not offline_mode else "Offline",
                  f"{((soxl_price-prev_close)/prev_close)*100:.2f}%" if not offline_mode and prev_close > 0 else "-")
        c3.metric("기준 슬롯",    f"${slot_sz:,.0f}")
        c4.metric("최종 조정 슬롯", f"${adj_slot:,.0f}",
                  f"QS×{curr_qs_mul:.1f} × LS×{curr_ls_mul:.1f}",
                  delta_color="normal" if (curr_qs_mul * curr_ls_mul) >= 1.0 else "inverse")
        c5.metric("매매 사이클",  f"{cycle}일차")
        if not offline_mode:
            st.markdown(f"""
            <div class="st-card" style="border-left: 5px solid {'#1e8e3e' if curr_qs_mul > 1.0 else ('#e65100' if curr_qs_mul < 1.0 else '#888')};">
                📡 <strong>QS_strength 신호 (SOXL/QQQ 30일 상대강도)</strong>
                &nbsp;&nbsp;|&nbsp;&nbsp;
                <span class="badge-{qs_badge}">{qs_lbl}</span>
            </div>
            """, unsafe_allow_html=True)
            recent_str = "".join(['🟢' if x else '🔴' for x in curr_recent]) if curr_recent else "(청산 이력 없음)"
            if curr_ls_mul < 1.0:
                st.markdown(f"""
                <div class="st-card" style="border-left: 5px solid #d93025;">
                    🛡️ <strong>Loss Streak Guard 작동 중</strong>
                    &nbsp;&nbsp;|&nbsp;&nbsp;
                    <span class="badge-ls-on">최근 {LOSS_STREAK_N}건 연속 손실 → 슬롯 ×{LOSS_STREAK_MUL}</span>
                    &nbsp;&nbsp;최근 청산: {recent_str}
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="st-card" style="border-left: 5px solid #888;">
                    🧭 <strong>Loss Streak Guard 대기 중</strong>
                    &nbsp;&nbsp;|&nbsp;&nbsp;
                    <span class="badge-ls-off">정상 (×1.0)</span>
                    &nbsp;&nbsp;최근 청산: {recent_str}
                </div>
                """, unsafe_allow_html=True)
            # ★ v10.0: 카마릴라 오버레이 상태 카드
            if curr_cama.get('enabled'):
                cama_today = curr_cama.get('today', {})
                if cama_today.get('has_open_position'):
                    st.markdown(f"""
                    <div class="st-card" style="border-left: 5px solid #e65100;">
                        🧨 <strong>카마릴라 R4 — 포지션 청산 대상</strong>
                        &nbsp;&nbsp;|&nbsp;&nbsp;
                        <span class="badge-cama-open">{cama_today['entry_date']} 진입분, 진입가 ${cama_today['entry_raw']:.2f}, 투입금 ${cama_today['invest_amt']:,.0f} → 다음 거래일 시가 청산</span>
                    </div>
                    """, unsafe_allow_html=True)
                elif cama_today.get('has_candidate'):
                    st.markdown(f"""
                    <div class="st-card" style="border-left: 5px solid #1a73e8;">
                        🧨 <strong>카마릴라 R4 — 신규진입 후보</strong>
                        &nbsp;&nbsp;|&nbsp;&nbsp;
                        <span class="badge-cama-cand">저항선 ${cama_today['resistance']:.2f} (오늘 고가 돌파 시 진입), 예상 투입금 ${cama_today.get('invest_amt_candidate', 0.0):,.0f}</span>
                    </div>
                    """, unsafe_allow_html=True)
                elif cama_today.get('filtered_out'):
                    st.markdown(f"""
                    <div class="st-card" style="border-left: 5px solid #888;">
                        🧨 <strong>카마릴라 R4 — 변동성 필터 미통과로 신규진입 제외</strong>
                        &nbsp;&nbsp;|&nbsp;&nbsp;
                        <span class="badge-cama-none">저항선 ${cama_today.get('resistance', float('nan')):.2f} (참고용, vol_rank={cama_today.get('vol_rank', float('nan')):.2f})</span>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="st-card" style="border-left: 5px solid #888;">
                        🧨 <strong>카마릴라 R4 — 오늘 후보 없음</strong>
                        &nbsp;&nbsp;|&nbsp;&nbsp;
                        <span class="badge-cama-none">대기 중</span>
                    </div>
                    """, unsafe_allow_html=True)
        st.markdown("---")
        order_date_str = today.strftime("%Y-%m-%d")
        st.subheader(f"📋 오늘의 주문 ({order_date_str})")
        if offline_mode:
            st.warning("오프라인 모드에서는 최신 주문을 생성할 수 없습니다.")
        else:
            df_h        = st.session_state['holdings']
            sell_orders = []
            buy_orders  = []
            if not df_h.empty:
                df_h['손절기한'] = pd.to_datetime(df_h['손절기한']).dt.date
                for idx, row in df_h.iterrows():
                    if row['손절기한'] <= today:
                        sell_orders.append(f"**[매도]** 티어{idx+1}: **{row['수량']}주** (시장가) - **MOC (기간만료)**")
                    else:
                        sell_orders.append(f"**[매도]** 티어{idx+1}: **{row['수량']}주** (${row['목표가']:.2f}) - **LOC (익절)**")
            if soxl_price > 0:
                b_lim = soxl_price * (1 + r['buy'] / 100)
                b_qty = int(adj_slot / b_lim)
                mul_parts = []
                if curr_qs_mul != 1.0: mul_parts.append(f"QS×{curr_qs_mul:.1f}")
                if curr_ls_mul < 1.0:  mul_parts.append(f"LS×{curr_ls_mul:.1f}")
                mul_note = f"  ← {' / '.join(mul_parts)}" if mul_parts else ""
                buy_orders.append(f"**[매수]** 신규: **{b_qty}주 (예상)** (${b_lim:.2f}) - **LOC**{mul_note}")
            # ★ v10.0: 카마릴라 청산/진입 주문
            if curr_cama.get('enabled'):
                cama_today = curr_cama.get('today', {})
                if cama_today.get('has_open_position'):
                    sell_orders.append(
                        f"**[매도]** 🧨 카마릴라: 투입금 **${cama_today['invest_amt']:,.0f}** - **MOO (다음 거래일 시가 무조건 청산)**")
                elif cama_today.get('has_candidate'):
                    buy_orders.append(
                        f"**[매수]** 🧨 카마릴라: 저항선 **${cama_today['resistance']:.2f}** 돌파 시 (예상 ${cama_today.get('invest_amt_candidate', 0.0):,.0f}) - **스탑 매수**")
            if not sell_orders and not buy_orders:
                st.info("오늘 예정된 주문이 없습니다.")
            else:
                for order in sell_orders:
                    st.markdown(f"""
                    <div class="st-card" style="border-left: 5px solid #d93025;">
                        <span class="badge-sell">매도</span> {order.replace('**[매도]**', '')}
                    </div>""", unsafe_allow_html=True)
                for order in buy_orders:
                    st.markdown(f"""
                    <div class="st-card" style="border-left: 5px solid #1e8e3e;">
                        <span class="badge-buy">매수</span> {order.replace('**[매수]**', '')}
                    </div>""", unsafe_allow_html=True)
        st.markdown("---")
        # ★ v9.8: 5월 양도세 인출 시기 자동 알림 계산
        df_j_for_tax = st.session_state.get('journal', pd.DataFrame())
        yearly_realized_for_tax = {}
        if not df_j_for_tax.empty and '날짜' in df_j_for_tax.columns and '수익금' in df_j_for_tax.columns:
            try:
                df_j_copy = df_j_for_tax.copy()
                df_j_copy['_year'] = pd.to_datetime(df_j_copy['날짜']).dt.year
                df_j_copy['_pnl'] = pd.to_numeric(df_j_copy['수익금'], errors='coerce').fillna(0)
                yearly_realized_for_tax = df_j_copy.groupby('_year')['_pnl'].sum().to_dict()
            except Exception:
                yearly_realized_for_tax = {}
        # 표준 한국 양도세 (250만원 / 1400 KRW = ~$1786 공제, 22%)
        DEFAULT_TAX_DEDUCTION_USD = 2_500_000 / 1400
        DEFAULT_TAX_RATE = 0.22
        last_yr = today.year - 1
        last_yr_realized = float(yearly_realized_for_tax.get(last_yr, 0.0))
        expected_tax_for_last_yr = max(0.0, last_yr_realized - DEFAULT_TAX_DEDUCTION_USD) * DEFAULT_TAX_RATE
        # 올해 이미 인출한 금액
        wd_df_pre = withdrawals_df.copy() if withdrawals_df is not None else pd.DataFrame(columns=["날짜","금액","메모"])
        already_paid_this_yr = 0.0
        if not wd_df_pre.empty:
            try:
                wd_df_pre['_yr'] = pd.to_datetime(wd_df_pre['날짜']).dt.year
                already_paid_this_yr = float(wd_df_pre[wd_df_pre['_yr'] == today.year]['금액'].sum())
            except Exception:
                pass
        remaining_tax_to_pay = max(0.0, expected_tax_for_last_yr - already_paid_this_yr)
        # 5월~8월(분납 기간) 사이 + 미납 잔액 있을 때 빨간 알림
        in_tax_payment_window = (today.month >= 5 and today.month <= 8)
        should_show_tax_alert = in_tax_payment_window and remaining_tax_to_pay > 0
        with st.expander(
            ("🔴 [양도세 인출 시기] " if should_show_tax_alert else "") +
            "💸 양도세 인출 기록 관리 (시트 동기화 → 봇 잔고 자동 반영)",
            expanded=should_show_tax_alert):
            # ★ v9.8: 5월 알림 패널
            if should_show_tax_alert:
                st.markdown(f"""
                <div style="background-color: rgba(217, 48, 37, 0.12); border-left: 6px solid #d93025;
                            padding: 18px 20px; border-radius: 10px; margin-bottom: 18px;">
                    <h3 style="margin: 0 0 10px 0; color: #d93025;">🔴 {last_yr}년분 양도세 인출 시기입니다</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                      <tr><td style="padding: 4px 0;">📊 <strong>{last_yr}년 실현손익:</strong></td>
                          <td style="padding: 4px 0; text-align: right;"><strong>${last_yr_realized:,.2f}</strong></td></tr>
                      <tr><td style="padding: 4px 0;">📉 공제 한도 (250만원 / 1400 KRW):</td>
                          <td style="padding: 4px 0; text-align: right;">${DEFAULT_TAX_DEDUCTION_USD:,.0f}</td></tr>
                      <tr><td style="padding: 4px 0;">📊 과세 대상 금액:</td>
                          <td style="padding: 4px 0; text-align: right;">${max(0, last_yr_realized - DEFAULT_TAX_DEDUCTION_USD):,.2f}</td></tr>
                      <tr><td style="padding: 4px 0;">💰 예상 양도세 (22%):</td>
                          <td style="padding: 4px 0; text-align: right;"><strong>${expected_tax_for_last_yr:,.2f}</strong></td></tr>
                      <tr><td style="padding: 4px 0;">✅ {today.year}년 이미 인출:</td>
                          <td style="padding: 4px 0; text-align: right;">${already_paid_this_yr:,.2f}</td></tr>
                      <tr style="border-top: 2px solid #d93025;">
                          <td style="padding: 8px 0 0 0;"><strong style="color: #d93025;">⚠️ 남은 인출 필요액:</strong></td>
                          <td style="padding: 8px 0 0 0; text-align: right;">
                              <strong style="color: #d93025; font-size: 1.2em;">${remaining_tax_to_pay:,.2f}</strong></td></tr>
                    </table>
                    <p style="margin: 10px 0 0 0; color: #d93025; font-size: 0.9em;">
                      📅 한국 양도세 신고 마감: <strong>5월 31일</strong> · 분납 마감: <strong>8월 31일</strong><br/>
                      💡 아래 표에서 인출 기록을 추가하면 알림이 사라집니다.
                    </p>
                </div>
                """, unsafe_allow_html=True)
            elif in_tax_payment_window and last_yr_realized > 0:
                # 알림 대상이지만 이미 인출 완료
                st.success(f"✅ {last_yr}년분 양도세 인출 완료 (예상 ${expected_tax_for_last_yr:,.0f} / 이미 ${already_paid_this_yr:,.0f} 인출)")
            wb_ok = get_gspread_workbook() is not None
            if not wb_ok:
                st.warning("⚠️ Streamlit Secrets 에 `GCP_CREDENTIALS` 가 없거나 시트 접근 권한이 부족합니다.")
            wd_df = withdrawals_df.copy() if withdrawals_df is not None else pd.DataFrame(columns=["날짜","금액","메모"])
            past = wd_df[pd.to_datetime(wd_df["날짜"]).dt.date <= today] if not wd_df.empty else pd.DataFrame()
            future = wd_df[pd.to_datetime(wd_df["날짜"]).dt.date > today] if not wd_df.empty else pd.DataFrame()
            past_total = float(past["금액"].sum()) if not past.empty else 0.0
            future_total = float(future["금액"].sum()) if not future.empty else 0.0
            wd_c1, wd_c2, wd_c3 = st.columns(3)
            wd_c1.metric("📤 누적 인출 (반영 완료)", f"${past_total:,.0f}",
                         f"{len(past)}건" if not past.empty else "0건")
            wd_c2.metric("📅 예정 인출 (미반영)", f"${future_total:,.0f}",
                         f"{len(future)}건" if not future.empty else "0건")
            if not future.empty:
                nx = future.sort_values("날짜").iloc[0]
                wd_c3.metric("⏭ 다음 예정", pd.to_datetime(nx["날짜"]).strftime("%Y-%m-%d"),
                             f"${float(nx['금액']):,.0f}")
            else:
                wd_c3.metric("⏭ 다음 예정", "—")
            st.caption(
                "💡 시트의 **TaxWithdrawals** 탭에 저장. 인출일 도달 시 시뮬 real_cash 차감 + RESET 가상 자본 차감."
            )
            display_df = wd_df.copy() if not wd_df.empty else pd.DataFrame(
                {"날짜": pd.Series(dtype="datetime64[ns]"),
                 "금액": pd.Series(dtype="float64"),
                 "메모": pd.Series(dtype="object")}
            )
            edited_wd = st.data_editor(
                display_df, num_rows="dynamic", use_container_width=True,
                key="wd_editor",
                column_config={
                    "날짜": st.column_config.DateColumn("날짜", format="YYYY-MM-DD", required=True),
                    "금액": st.column_config.NumberColumn("금액 ($)", format="$%.2f", min_value=0.0, required=True),
                    "메모": st.column_config.TextColumn("메모"),
                },
                disabled=not wb_ok,
            )
            if st.button("💾 인출 기록 저장 (시트 → 잔고 재계산)", disabled=not wb_ok):
                if save_tax_withdrawals(edited_wd):
                    st.session_state['auto_run_done'] = False
                    st.cache_resource.clear()
                    st.success("저장 완료. 시뮬레이션을 재실행합니다…")
                    st.rerun()
        st.markdown("---")
        st.subheader("📊 나의 티어 현황")
        df_h = st.session_state['holdings']
        if not df_h.empty:
            df_h['매수일'] = pd.to_datetime(df_h['매수일']).dt.date
            df_h.index     = range(1, len(df_h) + 1)
            df_h.index.name = "티어"
            if not offline_mode:
                current_yields = ((soxl_price - df_h['매수가']) / df_h['매수가'] * 100)
                df_h['수익률'] = [f"{'🔺' if y > 0 else '🔻'} {y:.2f} %" for y in current_yields]
                df_h['상태']   = [
                    "🚨 MOC 매도" if row['손절기한'] <= today else "🔵 LOC 대기"
                    for _, row in df_h.iterrows()
                ]
                total_qty      = df_h['수량'].sum()
                total_invested = (df_h['매수가'] * df_h['수량']).sum()
                avg_price      = total_invested / total_qty if total_qty > 0 else 0
                current_val    = total_qty * soxl_price
                total_profit   = current_val - total_invested
                total_yield_pct = (total_profit / total_invested * 100) if total_invested > 0 else 0
                st.markdown("#### 📌 전체 계좌 요약")
                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("총 보유수량",  f"{total_qty} 주")
                sc2.metric("통합 평단가",  f"${avg_price:,.2f}")
                sc3.metric("총 평가손익",  f"${total_profit:,.2f}")
                sc4.metric("평균 수익률",  f"{total_yield_pct:,.2f}%")
            st.markdown("👇 **보유 티어 상세 내역 (편집 가능)**")
            edited_h = st.data_editor(
                df_h, num_rows="dynamic", use_container_width=True, key="h_edit",
                column_config={
                    "수익률": st.column_config.TextColumn("수익률", disabled=True),
                    "매수가": st.column_config.NumberColumn(format="$%.2f"),
                    "목표가": st.column_config.NumberColumn(format="$%.1f"),
                    "상태":   st.column_config.TextColumn(disabled=True)
                }
            )
            if st.button("💾 티어 수정 저장 (GitHub)"):
                save_cols = ["매수일","모드","매수가","수량","목표가","손절기한"]
                save_csv(edited_h[save_cols], HOLDINGS_FILE)
                st.session_state['holdings'] = edited_h[save_cols]
                st.success("저장되었습니다!")
                st.rerun()
        else:
            st.info("현재 보유 중인 티어가 없습니다.")
        st.markdown("---")
        st.subheader("📝 매매 수익 기록장")
        df_j    = st.session_state['journal']
        df_eq   = st.session_state['equity_history']
        df_log  = st.session_state['action_log']
        init_prin = saved_init_cap
        if not df_j.empty:
            total_prof_j  = df_j['수익금'].sum()
            total_yield_j = (total_prof_j / init_prin * 100)
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            mc1.metric("🏁 시작 원금",   f"${init_prin:,.0f}")
            mc2.metric("💰 누적 수익금", f"${total_prof_j:,.2f}")
            mc3.metric("📈 총 수익률",   f"{total_yield_j:.1f}%")
            mc4.metric("💸 누적 인출",   f"${curr_cum_wd:,.0f}",
                       help="시뮬에 반영 완료된 양도세 인출 누적액")
            mc5.metric("💰 누적 배당",   f"${curr_cum_div:,.2f}",
                       help="★ v9.8: SOXL 배당 cash 재투자 누적액")
        else:
            st.info("아직 실현된 수익이 없습니다.")
        # ★ v10.0: 카마릴라 오버레이 손익 (동파법 가상자본 계산과는 분리된 별도 회계)
        if curr_cama.get('enabled'):
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("🧨 카마릴라 거래수", f"{curr_cama.get('trade_count', 0)}")
            wr = curr_cama.get('win_rate')
            cc2.metric("🧨 카마릴라 승률", f"{wr*100:.1f}%" if wr is not None else "─")
            cc3.metric("🧨 카마릴라 누적손익", f"${curr_cama.get('total_pnl', 0.0):,.2f}",
                       help="동파법 RESET_CYCLE 가상자본 계산에는 섞이지 않는 별도 회계")
        start_date_display = saved_start_date.strftime("%Y-%m-%d")
        with st.expander(f"📜 전략 시작일({start_date_display}) 이후 상세 매매 기록", expanded=False):
            if not df_log.empty:
                st.dataframe(df_log, use_container_width=True,
                             column_config={
                                 "구분": st.column_config.TextColumn("구분", width="small"),
                                 "비고": st.column_config.TextColumn("비고", width="medium"),
                             })
            else:
                st.caption("⚠️ 기록된 매매 내역이 없습니다.")
        st.markdown("### 📈 내 자산 성장 그래프")
        if not df_eq.empty:
            df_eq['날짜'] = pd.to_datetime(df_eq['날짜'])
            df_eq = df_eq.sort_values(by="날짜")
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(df_eq['날짜'], df_eq['총자산'], color='#4CAF50', linewidth=2)
            ax.fill_between(df_eq['날짜'], df_eq['총자산'], init_prin,
                            where=(df_eq['총자산'] >= init_prin), color='#4CAF50', alpha=0.1)
            ax.fill_between(df_eq['날짜'], df_eq['총자산'], init_prin,
                            where=(df_eq['총자산'] < init_prin),  color='red',     alpha=0.1)
            ax.axhline(y=init_prin, color='gray', linestyle='--', alpha=0.5, label='원금')
            ax.set_title("Total Equity Growth (v10.0 — dividend reinvested)", fontweight='bold')
            ax.grid(True, linestyle='--', alpha=0.3)
            ax.yaxis.set_major_formatter(mtick.StrMethodFormatter('${x:,.0f}'))
            st.pyplot(fig)
        else:
            st.info("그래프 데이터가 없습니다.")
    with tab_backtest:
        st.header("🧪 백테스트 성과분석 (v10.0)")
        if offline_mode:
            st.warning("오프라인 모드에서는 백테스트를 실행할 수 없습니다.")
        else:
            bt_init_cap = st.number_input("백테스트 초기 자본 ($)", value=10000.0, step=1000.0)
            bc1, bc2   = st.columns(2)
            start_d    = bc1.date_input("검증 시작일", value=datetime(2010, 1, 1), min_value=datetime(2000, 1, 1))
            end_d      = bc2.date_input("검증 종료일", value=today,               min_value=datetime(2000, 1, 1))
            with st.expander("⚙️ 수수료 & 양도세 옵션 (현실 반영, 백테스트 시뮬)", expanded=False):
                opt1, opt2 = st.columns(2)
                with opt1:
                    inc_fees = st.checkbox("거래 수수료 적용", value=False,
                                           help="매수 0.015% / 매도 0.015% + SEC fee 0.00206% = 0.01706%")
                    if inc_fees:
                        st.caption(f"매수: 0.015% / 매도: 0.01706% (commission + SEC fee)")
                with opt2:
                    inc_tax = st.checkbox("양도세 적용 (시뮬)", value=False,
                                          help="백테스트용 자동 시뮬레이션.")
                    if inc_tax:
                        krw_rate = st.number_input("USD/KRW 환율", value=1400, min_value=800, max_value=2000, step=10)
                        ded_krw  = st.number_input("연 공제 한도 (KRW)", value=2_500_000, min_value=0, step=100_000)
                        ded_usd  = ded_krw / krw_rate
                        st.caption(f"공제 USD 환산: ${ded_usd:,.0f} | 세율: 22%")
                        tax_strategy = st.selectbox(
                            "양도세 인출 전략 (백테스트 시뮬)",
                            options=['A', 'B'], index=0,
                            format_func=lambda x: {
                                'A': 'A: 5월 일괄인출 (default)',
                                'B': '🎨 B: 커스텀 (직접 월 선택)',
                            }[x],
                        )
                        # ★ v9.8: B (CUSTOM) 전략 — 월 체크박스 + 균등 분할
                        custom_schedule = None
                        if tax_strategy == 'B':
                            st.markdown("##### 🎨 인출 월 선택 (선택한 개수만큼 균등 분할)")
                            month_defs = [
                                ('Dec', '전년 12월', 12, -1),  # year_offset = -1 (전년)
                                ('Jan', '1월', 1, 0),
                                ('Feb', '2월', 2, 0),
                                ('Mar', '3월', 3, 0),
                                ('Apr', '4월', 4, 0),
                                ('May', '5월', 5, 0),
                                ('Jun', '6월', 6, 0),
                                ('Jul', '7월', 7, 0),
                                ('Aug', '8월', 8, 0),
                            ]
                            cb_cols = st.columns(9)
                            selected_months = []
                            for i, (key, lbl, mnum, yoff) in enumerate(month_defs):
                                checked = cb_cols[i].checkbox(lbl, value=False, key=f"tax_m_{key}")
                                if checked:
                                    selected_months.append((key, lbl, mnum, yoff))
                            if len(selected_months) == 0:
                                st.warning("⚠️ 최소 1개 월을 선택해주세요. 선택 없을 시 A(5월 일괄) 으로 fallback.")
                                tax_strategy = 'A'
                            else:
                                n_sel = len(selected_months)
                                frac_each = 1.0 / n_sel
                                custom_schedule = []
                                for key, lbl, mnum, yoff in selected_months:
                                    if yoff == -1:
                                        # 전년 12월: 연 전환 시점에 즉시 차감 (4-tuple)
                                        custom_schedule.append(
                                            (frac_each, (12, 1), (12, 31), -1)
                                        )
                                    else:
                                        # force_date 는 8/31 로 설정 (한국 양도세 분납 마지막 시점 고려)
                                        custom_schedule.append(
                                            (frac_each, (mnum, 1), (8, 31))
                                        )
                                month_list_str = ', '.join(m[1] for m in selected_months)
                                st.info(f"✅ 선택: **{month_list_str}** ({n_sel}개 월 × {frac_each*100:.1f}% 균등 분할)")
                    else:
                        ded_usd  = 1786.0
                        krw_rate = 1400
                        tax_strategy = 'A'
                        custom_schedule = None
            # ★ v10.0: 카마릴라 R4 오버레이 (백테스트는 저장된 설정과 무관하게 자유롭게 켜고 끌 수 있음)
            with st.expander("🧨 카마릴라 R4 오버레이 (백테스트)", expanded=False):
                bt_overlay_enabled = st.checkbox(
                    "백테스트에 오버레이 포함", value=False, key="bt_overlay_enabled")
                bt_overlay_fraction = st.slider(
                    "투입 비중 (남은 현금 중 %)", 0.0, 1.0, OVERLAY_DEFAULTS['overlay_fraction'], 0.05,
                    disabled=not bt_overlay_enabled, key="bt_overlay_fraction")
                bt_cama_coef = st.slider(
                    "R4 저항선 계수 (coef)", 0.10, 2.00, OVERLAY_DEFAULTS['cama_coef'], 0.05,
                    disabled=not bt_overlay_enabled, key="bt_cama_coef")
                bt_use_vol_filter = st.checkbox(
                    "변동성 백분위 필터 사용", value=True,
                    disabled=not bt_overlay_enabled, key="bt_use_vol_filter")
                if bt_use_vol_filter:
                    bt_cama_vol_filter_pct = st.slider(
                        "변동성 백분위 상한", 0.0, 1.0, OVERLAY_DEFAULTS['cama_vol_filter_pct'], 0.05,
                        disabled=not bt_overlay_enabled, key="bt_vol_pct")
                else:
                    bt_cama_vol_filter_pct = None
                bt_cama_fee_rate = st.number_input(
                    "왕복 수수료율 (편도)", value=OVERLAY_DEFAULTS['cama_fee_rate'], step=0.0001, format="%.4f",
                    disabled=not bt_overlay_enabled, key="bt_fee_rate")
                bt_cama_slippage_pct = st.number_input(
                    "슬리피지율 (편도)", value=OVERLAY_DEFAULTS['cama_slippage_pct'], step=0.0001, format="%.4f",
                    disabled=not bt_overlay_enabled, key="bt_slip_pct")
            if st.button("🚀 분석 실행"):
                with st.spinner("분석 중..."):
                    res, metrics, df_yearly, df_debug, df_cama = run_backtest_fixed(
                        df, start_d, end_d, bt_init_cap,
                        include_fees=inc_fees, include_tax=inc_tax,
                        tax_deduction_usd=ded_usd, tax_rate=0.22,
                        tax_strategy=tax_strategy,
                        custom_schedule=custom_schedule if inc_tax else None,
                        overlay_enabled=bt_overlay_enabled, overlay_fraction=bt_overlay_fraction,
                        cama_coef=bt_cama_coef, cama_vol_filter_pct=bt_cama_vol_filter_pct,
                        cama_fee_rate=bt_cama_fee_rate, cama_slippage_pct=bt_cama_slippage_pct)
                    if res is not None:
                        final  = res['Equity'].iloc[-1]
                        ret    = (final / bt_init_cap) - 1
                        days   = (res.index[-1] - res.index[0]).days
                        cagr   = (1 + ret) ** (365 / days) - 1 if days > 0 else 0
                        res['Peak']     = res['Equity'].cummax()
                        res['Drawdown'] = (res['Equity'] - res['Peak']) / res['Peak']
                        mdd    = res['Drawdown'].min()
                        calmar = cagr / abs(mdd) if mdd != 0 else 0
                        m1, m2, m3, m4, m5, m6 = st.columns(6)
                        m1.metric("최종 수익금",   f"${final:,.0f}",        f"{ret*100:,.1f}%")
                        m2.metric("CAGR",          f"{cagr*100:.2f}%")
                        m3.metric("MDD",           f"{mdd*100:.2f}%",       delta_color="inverse")
                        m4.metric("Calmar",        f"{calmar:.2f}")
                        m5.metric("Sortino",       f"{metrics['sortino']:.2f}")
                        m6.metric("Profit Factor", f"{metrics['profit_factor']:.2f}")
                        # ★ v9.8: 배당 정보
                        st.markdown(f"#### 💰 부수 효과")
                        dc1, dc2 = st.columns(2)
                        dc1.metric("누적 배당 (재투자)", f"${metrics.get('total_dividends', 0):,.0f}",
                                   help="시뮬 기간 동안 SOXL 배당으로 받은 cash (자동 재투자됨)")
                        dc2.metric("배당이 자산에서 차지하는 비율",
                                   f"{(metrics.get('total_dividends', 0) / final * 100):.2f}%" if final > 0 else "─")
                        if metrics.get('include_fees') or metrics.get('include_tax'):
                            strategy_label = {
                                'A': 'A: 5월 일괄인출',
                                'B': '🎨 B: 커스텀 (직접 선택)',
                            }.get(metrics.get('tax_strategy', 'A'), 'A')
                            st.markdown(f"#### 💰 수수료 & 양도세 (시뮬)" +
                                        (f" — 인출 전략: **{strategy_label}**" if metrics.get('include_tax') else ""))
                            tc1, tc2, tc3, tc4 = st.columns(4)
                            tc1.metric("누적 수수료",
                                       f"${metrics['total_fees']:,.0f}",
                                       f"매수 ${metrics['total_buy_fees']:,.0f} / 매도 ${metrics['total_sell_fees']:,.0f}"
                                       if metrics.get('include_fees') else "─ (미적용)",
                                       delta_color="off")
                            tc2.metric("누적 양도세",
                                       f"${metrics['total_tax_paid']:,.0f}" if metrics.get('include_tax') else "─",
                                       f"세율 22% / 연 ${ded_usd:,.0f} 공제" if metrics.get('include_tax') else "(미적용)",
                                       delta_color="off")
                            tc3.metric("미정산 양도세",
                                       f"${metrics['tax_pending_end']:,.0f}" if metrics.get('include_tax') else "─",
                                       delta_color="off")
                            after_final = final - metrics['tax_pending_end']
                            after_ret   = (after_final / bt_init_cap) - 1
                            after_cagr  = (1 + after_ret) ** (365 / days) - 1 if days > 0 else 0
                            tc4.metric("세후 추정 CAGR",
                                       f"{after_cagr*100:.2f}%",
                                       f"기존 대비 {(after_cagr-cagr)*100:+.2f}pp",
                                       delta_color="off")
                        # ★ v10.0: 카마릴라 오버레이 결과
                        if metrics.get('overlay_enabled'):
                            st.markdown("#### 🧨 카마릴라 오버레이 결과")
                            oc1, oc2, oc3 = st.columns(3)
                            oc1.metric("카마릴라 거래수", f"{metrics['cama_trade_count']}")
                            cwr = metrics.get('cama_win_rate', np.nan)
                            oc2.metric("카마릴라 승률", f"{cwr*100:.1f}%" if not np.isnan(cwr) else "─")
                            oc3.metric("누적 카마릴라손익", f"${metrics['cama_total_pnl']:,.2f}",
                                       help="동파법 가상자본 계산과는 분리된 별도 회계")
                        st.markdown("#### 📊 통합 성과 차트")
                        fig, ax1 = plt.subplots(figsize=(12, 6))
                        color = 'tab:blue'
                        ax1.set_xlabel('Date')
                        ax1.set_ylabel('Total Equity ($)', color=color, fontweight='bold')
                        ax1.plot(res.index, res['Equity'], color=color, linewidth=1.5, label='Equity')
                        ax1.tick_params(axis='y', labelcolor=color)
                        ax1.yaxis.set_major_formatter(mtick.StrMethodFormatter('${x:,.0f}'))
                        ax1.grid(True, linestyle='--', alpha=0.3)
                        ax2 = ax1.twinx()
                        color = 'tab:red'
                        ax2.set_ylabel('Drawdown (%)', color=color, fontweight='bold')
                        ax2.fill_between(res.index, res['Drawdown'] * 100, 0, color=color, alpha=0.2)
                        ax2.tick_params(axis='y', labelcolor=color)
                        ax2.set_ylim(-100, 5)
                        ax2.yaxis.set_major_formatter(mtick.PercentFormatter())
                        plt.title("Portfolio Performance vs Risk (v10.0)", fontweight='bold')
                        plt.tight_layout()
                        st.pyplot(fig)
                        st.markdown("#### 📅 연도별 성과표")
                        df_yearly_fmt = df_yearly.copy()
                        df_yearly_fmt['수익률']  = df_yearly_fmt['수익률'].apply(lambda x: f"{x*100:.1f}%")
                        df_yearly_fmt['MDD']     = df_yearly_fmt['MDD'].apply(lambda x: f"{x*100:.1f}%")
                        df_yearly_fmt['기말자산'] = df_yearly_fmt['기말자산'].apply(lambda x: f"${x:,.0f}")
                        df_yearly_fmt['배당']    = df_yearly_fmt['배당'].apply(lambda x: f"${x:,.0f}")
                        if metrics.get('include_fees'):
                            df_yearly_fmt['수수료'] = df_yearly_fmt['수수료'].apply(lambda x: f"${x:,.0f}")
                        else:
                            df_yearly_fmt = df_yearly_fmt.drop(columns=['수수료'], errors='ignore')
                        if metrics.get('include_tax'):
                            df_yearly_fmt['양도세']   = df_yearly_fmt['양도세'].apply(lambda x: f"${x:,.0f}")
                            df_yearly_fmt['실현손익'] = df_yearly_fmt['실현손익'].apply(lambda x: f"${x:,.0f}")
                        else:
                            df_yearly_fmt = df_yearly_fmt.drop(columns=['양도세','실현손익'], errors='ignore')
                        # ★ v10.0: 오버레이 켰을 때만 카마릴라손익 컬럼 표시
                        if metrics.get('overlay_enabled'):
                            df_yearly_fmt['카마릴라손익'] = df_yearly_fmt['카마릴라손익'].apply(lambda x: f"${x:,.0f}")
                        else:
                            df_yearly_fmt = df_yearly_fmt.drop(columns=['카마릴라손익'], errors='ignore')
                        st.dataframe(df_yearly_fmt.T, use_container_width=True)
                        if df_debug is not None and not df_debug.empty:
                            n_buy  = (df_debug['Action'] == '🟢 매수').sum()
                            n_sell = (df_debug['Action'] == '🔴 매도').sum()
                            st.markdown(f"#### 📋 전체 매매 로그")
                            st.caption(f"총 매매 건수: **{len(df_debug):,}건** "
                                       f"(매수 {n_buy:,} / 매도 {n_sell:,})")
                            df_log_view = df_debug.sort_values('날짜', ascending=False).reset_index(drop=True)
                            st.dataframe(df_log_view, use_container_width=True, height=500, hide_index=True)
                        else:
                            st.info("매매 발생 없음")
                        # ★ v10.0: 카마릴라 거래 로그
                        if metrics.get('overlay_enabled') and df_cama is not None and not df_cama.empty:
                            with st.expander("🧨 카마릴라 거래 로그", expanded=False):
                                st.caption(f"총 카마릴라 거래: **{len(df_cama):,}건**")
                                st.dataframe(df_cama, use_container_width=True, hide_index=True)
                    else:
                        st.error("데이터 부족")
    with tab_logic:
        st.header("📚 동파법(Dongpa) 전략 매뉴얼 v10.0")
        st.markdown(f"""
        ### 1. 전략 개요
        * **핵심:** "시장의 계절(Mode)을 먼저 파악하고, 그에 맞는 옷(Rule)을 입는다."
        * **대상:** SOXL (3배 레버리지) / **지표:** QQQ (나스닥100)
        * **특징:** 대응 중심의 변동성 돌파 & 추세 추종 하이브리드.
        ---
        ### 2. ★ v9.8 fix — Dividend 재투자 + 정확한 splits 처리
        v9.6 ~ v9.7 에서 수동 split 적용이 yfinance 의 split 처리와 중복되어 백테스트가
        4~7배 거짓 inflate 되는 버그가 있었음. v9.8 은:
        | 항목 | 처리 방식 |
        | :--- | :--- |
        | **Splits** | **yfinance Close 가 이미 처리** (수동 처리 제거) |
        | **Dividends** | **시뮬 cash 주입** (ex-div 날짜에 배당 × 보유주식수 만큼 cash 증가) |
        | **봇/시뮬 일관성** | 유지 (auto_adjust=False 그대로) |
        | **배당 재투자** | 자동 (cash 가 늘면 다음 매수에 활용) |
        → 백테스트 결과가 정확해지고, 배당 효과도 자연스럽게 반영됨.
        ---
        ### 3. 시장 모드 / QS / LS / 모드별 분할
        - 시장 모드: 주봉 RSI(14) 기반
        - QS<{QS_LOW_THRESH}:×{QS_LOW_MULT} / QS>{QS_HIGH_THRESH}:×{QS_HIGH_MULT}(최대 {QS_HIGH_SLOT_CAP}슬롯)
        - LS: 최근 {LOSS_STREAK_N}건 연속 손실 시 ×{LOSS_STREAK_MUL}
        - Offense {MAX_SLOTS_OFFENSE}분할 / Safe {MAX_SLOTS_SAFE}분할
        - 복리 reset: {RESET_CYCLE}일
        ---
        ### 4. ★ v10.0 신규 — 카마릴라 R4 돌파매매 오버레이
        동파법과 **real_cash를 공유하는 단일 계좌 모델**로 통합되어 있으며, 별도 앱이 아니라 사이드바 토글로 ON/OFF 합니다.

        매일 루프 순서:
        1. **① 전날 청산** — 전날 열어둔 카마릴라 포지션을 오늘 시가로 무조건 청산 (왕복 수수료·슬리피지 반영), 회수된 현금은 즉시 real_cash로 합류.
        2. **② 동파법 본 로직** — 매도/매수를 원본 v9.8 그대로 실행 (오버레이 여부와 무관하게 100% 동일).
        3. **③ 신규 진입** — 동파법이 다 쓰고 남은 real_cash의 `overlay_fraction`(기본 70%)만큼, 오늘 Camarilla R4 저항선 돌파 신호가 있으면 신규 진입 (단일 슬롯).

        저항선 공식: `resistance[D] = 전일종가 + (전일고가-전일저가) × 1.1 × coef`, 신호는 `당일고가 ≥ resistance` (+선택적 변동성 백분위 필터). 진입가는 시가가 저항선 이상이면 시가, 아니면 저항선가. 청산은 무조건 다음 거래일 시가.

        `overlay_enabled=False`(기본값)이면 ①③ 스텝이 완전히 스킵되어 v9.8과 100% 동일하게 동작합니다 — 하위호환이 보장됩니다.

        settings.json의 `overlay_enabled`/`overlay_fraction`/`cama_coef`/`cama_vol_filter_pct`/`cama_fee_rate`/`cama_slippage_pct` 키는 **bot.py(v10.0)와 공유**되므로, 사이드바에서 토글하면 다음 실제 매매 봇 실행 때도 즉시 반영됩니다 (Single Source of Truth).

        카마릴라는 수수료·슬리피지(왕복)만 반영하고 **세금은 시뮬레이션하지 않습니다** — 실제 양도세는 동파법과 마찬가지로 TaxWithdrawals 시트로 수동 추적합니다. RESET_CYCLE 가상자본 계산에도 카마릴라 손익은 섞지 않습니다(회계 분리).
        """)


if __name__ == "__main__":
    main()
