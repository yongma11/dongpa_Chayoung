# dual_sniper.py — Dual Sniper Pro 백테스트 Streamlit 앱
# 전략 로직: 공격/방어 2모드, RSI 정규화 보유기간/매도조건, 5-티어 독립 운용
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import io

st.set_page_config(page_title="Dual Sniper Pro", page_icon="🎯", layout="wide")
st.markdown("""
<style>
  @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.8/dist/web/static/pretendard.css");
  html, body, [class*="css"] { font-family: 'Pretendard', sans-serif; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# 기본 파라미터 상수
# ─────────────────────────────────────────────
DEFAULT = dict(
    ticker        = "SOXL",
    init_cash     = 10000.0,
    # 공격모드
    atk_tiers     = 5,
    atk_buy_pct   = 0.5,    # FI+ 시 매수조건 (전일 종가 대비 %)
    atk_fi_neg    = -0.1,   # FI- 시 매수조건 (고정)
    atk_hold_nmin = 7,      # 보유기간 최소(일)
    atk_hold_nmax = 30,     # 보유기간 최대(일)
    atk_hold_a    = 2.0,    # 보유기간 α
    atk_sell_min  = 0.1,    # 매도조건 최소(%)
    atk_sell_max  = 3.0,    # 매도조건 최대(%)
    atk_sell_a    = 0.4,    # 매도조건 α
    atk_ma_period = 5,      # 매도보류 MA 기준
    # 방어모드
    def_tiers     = 5,
    def_hold      = 8,      # 보유기간(일, 고정)
    def_buy_ma    = -0.6,   # MA 대비 매수조건(%)
    def_buy_prev  = -5.5,   # 전일 종가 대비 매수조건(%)
    def_sell_pct  = 0.7,    # MA 대비 매도조건(%)
    def_ma_period = 3,      # MA 기준
    def_weights   = [6,13,20,27,34],  # 티어별 비중(%)
    # 모드 전환
    mode_rsi_up   = 55.0,   # wRSI 상향 돌파 시 공격모드
    mode_rsi_dn   = 50.0,   # wRSI 하향 돌파 시 방어모드
    rsi_period    = 14,
)

# ─────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────
def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_ma(series, period):
    return series.rolling(period).mean()

def load_data(ticker, start, end, rsi_period=14, mode_up=55, mode_dn=50):
    """yfinance 데이터 로드 + 지표 계산"""
    raw = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if raw.empty:
        return None
    df = pd.DataFrame({
        'Open' : raw['Open'].squeeze(),
        'High' : raw['High'].squeeze(),
        'Low'  : raw['Low'].squeeze(),
        'Close': raw['Close'].squeeze(),
        'Volume': raw['Volume'].squeeze(),
    }).dropna()
    df.index = pd.to_datetime(df.index)

    # 일봉 RSI
    df['RSI'] = calc_rsi(df['Close'], rsi_period)

    # Force Index (부호만: + or -)
    df['FI_pos'] = (df['Close'] >= df['Close'].shift(1))   # True=양수, False=음수

    # MA(3), MA(5)
    df['MA3'] = calc_ma(df['Close'], 3)
    df['MA5'] = calc_ma(df['Close'], 5)

    # 주봉 RSI (weekly resample → RSI)
    weekly = df['Close'].resample('W-FRI').last().dropna()
    wrsi   = calc_rsi(weekly, rsi_period).rename('wRSI')
    df     = df.join(wrsi.resample('D').last().ffill(), how='left')

    # 모드 전환 (wRSI 기반 히스테레시스)
    mode = []
    cur  = '방어'
    for w in df['wRSI']:
        if pd.isna(w):
            mode.append(cur)
            continue
        if cur == '방어' and w >= mode_up:
            cur = '공격'
        elif cur == '공격' and w < mode_dn:
            cur = '방어'
        mode.append(cur)
    df['Mode'] = mode

    return df

def load_data_from_log(log_df):
    """업로드된 Dual Sniper 로그에서 모드/가격 시리즈 추출"""
    log = log_df.copy()
    log = log[~log['날짜'].isna()].copy()
    log = log[log['날짜'].str.match(r'\d{2}-\d{2}-\d{2}', na=False)].copy()
    log['날짜'] = '20' + log['날짜'].str.split(' ').str[0]
    log['날짜'] = pd.to_datetime(log['날짜'], format='%Y-%m-%d', errors='coerce')
    log = log.dropna(subset=['날짜']).set_index('날짜')

    close_s = log['종가'].str.replace('$','',regex=False).astype(float)
    mode_s  = log['모드']
    rsi_s   = pd.to_numeric(log['일RSI'], errors='coerce')
    fi_pos_s = (log['FI'] == '=+')   # True=양수 FI
    ma3_s   = pd.to_numeric(log['MA(n)'], errors='coerce')
    ma5_s   = pd.to_numeric(log['MA(5)'], errors='coerce')

    out = pd.DataFrame({
        'Close':   close_s,
        'RSI':     rsi_s,
        'FI_pos':  fi_pos_s,
        'MA3':     ma3_s,
        'MA5':     ma5_s,
        'Mode':    mode_s,
    })
    return out.dropna(subset=['Close'])

# ─────────────────────────────────────────────
# 정규화 공식
# ─────────────────────────────────────────────
RSI_LOW   = 35
RSI_RANGE = 30

def rsi_x(rsi):
    return max(0.0, min(1.0, (rsi - RSI_LOW) / RSI_RANGE))

def calc_hold_days(rsi, n_min, n_max, alpha):
    x = rsi_x(rsi)
    return max(1, int(round(n_min + (n_max - n_min) * (1 - x) ** (1 / alpha))))

def calc_sell_pct(rsi, sell_min, sell_max, alpha):
    x = rsi_x(rsi)
    return sell_min + (sell_max - sell_min) * (1 - x) ** (1 / alpha)

# ─────────────────────────────────────────────
# 백테스트 엔진
# ─────────────────────────────────────────────
def run_backtest(df, p):
    """
    df: Close, RSI, FI_pos, MA3, MA5, Mode 컬럼 포함
    p : 파라미터 dict
    """
    init_cash    = p['init_cash']
    atk_tiers    = p['atk_tiers']
    def_tiers    = p['def_tiers']
    def_weights  = [w/100 for w in p['def_weights']]
    # 방어 티어 누적 비중 (할당 계산용)
    def_cum      = [sum(def_weights[:i+1]) for i in range(def_tiers)]

    dates  = df.index
    closes = df['Close'].values
    rsis   = df['RSI'].values
    fi_pos = df['FI_pos'].values    # True=FI양수, False=FI음수(매수 신호)
    ma3    = df['MA3'].values
    ma5    = df['MA5'].values
    modes  = df['Mode'].values

    cash   = init_cash
    log    = []

    # 슬롯: {'buy_date_idx','buy_price','sell_target','hold_until_idx','shares','tier','mode','rsi','hold_days'}
    atk_slots = [None] * atk_tiers
    def_slots = [None] * def_tiers

    # 방어 티어별 다음 빈 슬롯 인덱스 추적
    def_next_tier = 0   # 다음에 채울 티어 번호 (0-based)
    atk_next_tier = 0

    total_assets_arr = []

    for i, date in enumerate(dates):
        close = closes[i]
        rsi   = rsis[i]
        mode  = modes[i]
        fi_p  = fi_pos[i]
        _ma3  = ma3[i]
        _ma5  = ma5[i]
        prev_close = closes[i-1] if i > 0 else close
        prev2_close = closes[i-2] if i > 1 else prev_close

        # ── 오늘 MOC 주문 처리 전, 평가 ──
        holdings_val = 0.0
        for s in atk_slots + def_slots:
            if s is not None:
                holdings_val += s['shares'] * close

        # ─── 매도 처리 ───
        sold_tiers_today = set()   # MOC 매도된 티어

        # 공격모드 슬롯 매도 체크
        if mode == '공격':
            for ti in range(atk_tiers):
                s = atk_slots[ti]
                if s is None:
                    continue
                if s['mode'] != '공격':
                    # 모드 전환으로 남은 포지션 → 즉시 청산
                    cash += s['shares'] * close
                    log.append({'날짜': date, 'Action': '🔄모드청산', '티어': f'공T{ti+1}',
                                '종가': close, '수량': s['shares'], '손익': (close-s['buy_price'])*s['shares'],
                                '사유': '모드전환', 'Cash': cash, 'Holdings': holdings_val})
                    atk_slots[ti] = None
                    continue
                # 보유기간 만료 (MOC)
                if i >= s['hold_until_idx']:
                    pnl  = (close - s['buy_price']) * s['shares']
                    cash += s['shares'] * close
                    log.append({'날짜': date, 'Action': '🔴매도(공)', '티어': f'공T{ti+1}',
                                '종가': close, '수량': s['shares'], '손익': pnl,
                                '사유': 'MOC', 'Cash': cash, 'Holdings': 0})
                    atk_slots[ti] = None
                    sold_tiers_today.add(('공', ti))
                    continue
                # 수익 목표 달성
                if close >= s['sell_target']:
                    # MA5 보류 조건: 전일 종가 > 전일 MA5 이면 T1만 보류
                    if ti == 0 and i > 0 and closes[i-1] > (ma5[i-1] if not np.isnan(ma5[i-1]) else 0):
                        pass   # 보류
                    else:
                        pnl  = (close - s['buy_price']) * s['shares']
                        cash += s['shares'] * close
                        log.append({'날짜': date, 'Action': '🔴매도(공)', '티어': f'공T{ti+1}',
                                    '종가': close, '수량': s['shares'], '손익': pnl,
                                    '사유': f'수익({s["sell_pct"]:.2f}%)', 'Cash': cash, 'Holdings': 0})
                        atk_slots[ti] = None
                        sold_tiers_today.add(('공', ti))

        # 방어모드 슬롯 매도 체크
        for ti in range(def_tiers):
            s = def_slots[ti]
            if s is None:
                continue
            # 모드가 방어→공격 전환되었어도 방어 포지션은 유지 (별도 풀)
            # 보유기간 만료 (MOC)
            if i >= s['hold_until_idx']:
                pnl  = (close - s['buy_price']) * s['shares']
                cash += s['shares'] * close
                log.append({'날짜': date, 'Action': '🔴매도(방)', '티어': f'방T{ti+1}',
                            '종가': close, '수량': s['shares'], '손익': pnl,
                            '사유': 'MOC', 'Cash': cash, 'Holdings': 0})
                def_slots[ti] = None
                sold_tiers_today.add(('방', ti))
                continue
            # MA 매도 조건
            factor = 1 + p['def_sell_pct'] / 100
            if not (np.isnan(prev_close) or np.isnan(prev2_close)):
                sell_trigger = factor * (prev_close + prev2_close) / (p['def_ma_period'] - factor)
                if close >= sell_trigger:
                    pnl  = (close - s['buy_price']) * s['shares']
                    cash += s['shares'] * close
                    log.append({'날짜': date, 'Action': '🔴매도(방)', '티어': f'방T{ti+1}',
                                '종가': close, '수량': s['shares'], '손익': pnl,
                                '사유': 'MA', 'Cash': cash, 'Holdings': 0})
                    def_slots[ti] = None
                    sold_tiers_today.add(('방', ti))

        # ─── 매수 처리 ───
        holdings_val = sum(s['shares']*close for s in atk_slots+def_slots if s is not None)

        if mode == '공격':
            # FI 음수(하락)일 때 다음 빈 티어 채우기
            buy_cond_pct = p['atk_fi_neg'] if not fi_p else p['atk_buy_pct']
            buy_trigger  = prev_close * (1 + buy_cond_pct / 100)
            if close <= buy_trigger:
                # 비어 있는 첫 티어 찾기
                for ti in range(atk_tiers):
                    if atk_slots[ti] is None:
                        total_val   = cash + holdings_val
                        alloc       = total_val / atk_tiers
                        if alloc < 1 or cash < alloc:
                            alloc = cash
                        if alloc < 1:
                            break
                        shares      = alloc / close
                        hold_days   = calc_hold_days(rsi, p['atk_hold_nmin'], p['atk_hold_nmax'], p['atk_hold_a'])
                        sell_pct    = calc_sell_pct(rsi, p['atk_sell_min'], p['atk_sell_max'], p['atk_sell_a'])
                        sell_target = close * (1 + sell_pct / 100)
                        hold_until  = i + hold_days   # 인덱스 기준
                        cash       -= alloc
                        atk_slots[ti] = {
                            'buy_price': close, 'sell_target': sell_target,
                            'hold_until_idx': hold_until, 'shares': shares,
                            'mode': '공격', 'rsi': rsi, 'hold_days': hold_days,
                            'sell_pct': sell_pct,
                        }
                        log.append({'날짜': date, 'Action': '🟢매수(공)', '티어': f'공T{ti+1}',
                                    '종가': close, '수량': shares,
                                    '손익': 0, '사유': f'RSI{rsi:.0f}→{hold_days}일/{sell_pct:.2f}%',
                                    'Cash': cash, 'Holdings': sum(s['shares']*close for s in atk_slots if s)})
                        break   # 하루 1티어만

        else:  # 방어모드
            if not np.isnan(_ma3):
                cond1 = _ma3 * (1 + p['def_buy_ma'] / 100)
                cond2 = prev_close * (1 + p['def_buy_prev'] / 100)
                buy_trigger = min(cond1, cond2)
                if close <= buy_trigger:
                    for ti in range(def_tiers):
                        if def_slots[ti] is None:
                            total_val = cash + holdings_val
                            w = def_weights[ti]
                            alloc = total_val * w
                            if alloc > cash:
                                alloc = cash
                            if alloc < 1:
                                break
                            shares     = alloc / close
                            hold_until = i + p['def_hold']
                            cash      -= alloc
                            def_slots[ti] = {
                                'buy_price': close, 'sell_target': None,
                                'hold_until_idx': hold_until, 'shares': shares,
                                'mode': '방어', 'rsi': rsi, 'hold_days': p['def_hold'],
                            }
                            log.append({'날짜': date, 'Action': '🟢매수(방)', '티어': f'방T{ti+1}',
                                        '종가': close, '수량': shares,
                                        '손익': 0, '사유': f'MA{p["def_ma_period"]} / {p["def_buy_ma"]}%',
                                        'Cash': cash, 'Holdings': sum(s['shares']*close for s in def_slots if s)})
                            break   # 하루 1티어만

        # 자산 계산
        holdings_val = sum(s['shares']*close for s in atk_slots+def_slots if s is not None)
        total_val = cash + holdings_val
        total_assets_arr.append({'날짜': date, 'Total_Asset': total_val,
                                  'Cash': cash, 'Mode': mode})

    # DataFrame 변환
    asset_df = pd.DataFrame(total_assets_arr).set_index('날짜')
    log_df   = pd.DataFrame(log) if log else pd.DataFrame()
    return asset_df, log_df

# ─────────────────────────────────────────────
# 성과 계산
# ─────────────────────────────────────────────
def calc_metrics(asset_df, init_cash):
    ta    = asset_df['Total_Asset']
    total_ret   = (ta.iloc[-1] / init_cash - 1) * 100
    years = (ta.index[-1] - ta.index[0]).days / 365.25
    cagr  = ((ta.iloc[-1]/init_cash)**(1/years) - 1)*100 if years > 0 else 0
    peak  = ta.cummax()
    dd    = (ta - peak) / peak * 100
    mdd   = dd.min()
    calmar = cagr / abs(mdd) if mdd != 0 else 0

    # 연도별
    yearly = {}
    for yr, grp in asset_df.groupby(asset_df.index.year):
        start_val = init_cash if yr == asset_df.index.year.min() else \
                    asset_df[asset_df.index.year < yr]['Total_Asset'].iloc[-1]
        end_val   = grp['Total_Asset'].iloc[-1]
        ann_ret   = (end_val / start_val - 1) * 100
        peak_yr   = grp['Total_Asset'].cummax()
        mdd_yr    = ((grp['Total_Asset'] - peak_yr) / peak_yr * 100).min()
        yearly[yr] = {'수익률': ann_ret, 'MDD': mdd_yr, '기말자산': end_val}

    return {
        'total_ret': total_ret,
        'cagr': cagr,
        'mdd': mdd,
        'calmar': calmar,
        'final_asset': ta.iloc[-1],
        'yearly': yearly,
    }

# ─────────────────────────────────────────────
# 보유기간/매도조건 미리보기 테이블
# ─────────────────────────────────────────────
def preview_table(p):
    rows = []
    for rsi in [35, 40, 45, 50, 55, 60, 65, 70]:
        h = calc_hold_days(rsi, p['atk_hold_nmin'], p['atk_hold_nmax'], p['atk_hold_a'])
        s = calc_sell_pct(rsi, p['atk_sell_min'], p['atk_sell_max'], p['atk_sell_a'])
        rows.append({'매수RSI': rsi, '보유일': h, '매도기준(%)': round(s,2)})
    return pd.DataFrame(rows)

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("🎯 Dual Sniper Pro 백테스트")
st.caption("공격/방어 2모드 · RSI 정규화 보유기간/매도조건 · 5-티어 독립 운용")

# ── 사이드바 ──
with st.sidebar:
    st.header("⚙️ 파라미터")

    with st.expander("📊 데이터 설정", expanded=True):
        ticker     = st.text_input("티커", DEFAULT['ticker'])
        col1, col2 = st.columns(2)
        start_date = col1.date_input("시작일", datetime(2016,1,1))
        end_date   = col2.date_input("종료일", datetime.today())
        init_cash  = st.number_input("초기 자금($)", value=DEFAULT['init_cash'], step=1000.0)

    with st.expander("🗂 모드 설정"):
        mode_src = st.radio("모드 소스", ["자동(wRSI 기반)", "로그 CSV 업로드"])
        log_file = None
        if mode_src == "로그 CSV 업로드":
            log_file = st.file_uploader("Dual Sniper 로그 CSV", type="csv")
        else:
            col1, col2 = st.columns(2)
            mode_up = col1.number_input("공격 진입(wRSI↑)", value=DEFAULT['mode_rsi_up'], step=1.0)
            mode_dn = col2.number_input("방어 진입(wRSI↓)", value=DEFAULT['mode_rsi_dn'], step=1.0)

    with st.expander("⚔️ 공격모드 파라미터"):
        atk_tiers   = st.number_input("분할수", value=DEFAULT['atk_tiers'], min_value=1, max_value=10)
        col1, col2  = st.columns(2)
        atk_buy_pct = col1.number_input("매수조건 FI+(%, 전일종가 대비)", value=DEFAULT['atk_buy_pct'])
        atk_fi_neg  = col2.number_input("매수조건 FI-(%, 고정)", value=DEFAULT['atk_fi_neg'])
        st.markdown("**보유기간 정규화**")
        col1, col2, col3 = st.columns(3)
        atk_hold_nmin = col1.number_input("n_min(일)", value=DEFAULT['atk_hold_nmin'])
        atk_hold_nmax = col2.number_input("n_max(일)", value=DEFAULT['atk_hold_nmax'])
        atk_hold_a    = col3.number_input("α(보유)", value=DEFAULT['atk_hold_a'], step=0.1)
        st.markdown("**매도조건 정규화**")
        col1, col2, col3 = st.columns(3)
        atk_sell_min = col1.number_input("sell_min(%)", value=DEFAULT['atk_sell_min'], step=0.05)
        atk_sell_max = col2.number_input("sell_max(%)", value=DEFAULT['atk_sell_max'], step=0.1)
        atk_sell_a   = col3.number_input("α(매도)", value=DEFAULT['atk_sell_a'], step=0.05)
        atk_ma_period = st.number_input("매도보류 MA 기준", value=DEFAULT['atk_ma_period'])
        # 미리보기
        prev_p = dict(atk_hold_nmin=atk_hold_nmin, atk_hold_nmax=atk_hold_nmax,
                      atk_hold_a=atk_hold_a, atk_sell_min=atk_sell_min,
                      atk_sell_max=atk_sell_max, atk_sell_a=atk_sell_a)
        st.dataframe(preview_table(prev_p), hide_index=True, use_container_width=True)

    with st.expander("🛡 방어모드 파라미터"):
        def_tiers   = st.number_input("분할수 ", value=DEFAULT['def_tiers'], min_value=1, max_value=10)
        def_hold    = st.number_input("보유기간(일)", value=DEFAULT['def_hold'])
        col1, col2  = st.columns(2)
        def_buy_ma  = col1.number_input("매수조건 MA(%)", value=DEFAULT['def_buy_ma'])
        def_buy_prev= col2.number_input("매수조건 전일(%)", value=DEFAULT['def_buy_prev'])
        col1, col2  = st.columns(2)
        def_sell_pct= col1.number_input("매도조건 MA(%)", value=DEFAULT['def_sell_pct'])
        def_ma_period= col2.number_input("MA 기준(일)", value=DEFAULT['def_ma_period'])
        def_weights_str = st.text_input("티어 비중(%, 쉼표구분)", value="6,13,20,27,34")

    run_btn = st.button("▶️ 백테스트 실행", type="primary", use_container_width=True)

# ── 탭 레이아웃 ──
tab_result, tab_log, tab_compare, tab_logic = st.tabs(
    ["📈 백테스트 결과", "📋 매매 로그", "🔍 로그 비교", "📖 전략 로직"]
)

# ── 백테스트 실행 ──
if run_btn:
    # 파라미터 패킹
    try:
        def_w = [int(x.strip()) for x in def_weights_str.split(',')]
    except:
        def_w = DEFAULT['def_weights']

    params = dict(
        init_cash     = float(init_cash),
        atk_tiers     = int(atk_tiers),
        atk_buy_pct   = float(atk_buy_pct),
        atk_fi_neg    = float(atk_fi_neg),
        atk_hold_nmin = int(atk_hold_nmin),
        atk_hold_nmax = int(atk_hold_nmax),
        atk_hold_a    = float(atk_hold_a),
        atk_sell_min  = float(atk_sell_min),
        atk_sell_max  = float(atk_sell_max),
        atk_sell_a    = float(atk_sell_a),
        atk_ma_period = int(atk_ma_period),
        def_tiers     = int(def_tiers),
        def_hold      = int(def_hold),
        def_buy_ma    = float(def_buy_ma),
        def_buy_prev  = float(def_buy_prev),
        def_sell_pct  = float(def_sell_pct),
        def_ma_period = int(def_ma_period),
        def_weights   = def_w,
    )

    with st.spinner("데이터 로드 중..."):
        if mode_src == "로그 CSV 업로드" and log_file is not None:
            raw_log = pd.read_csv(log_file)
            df_data = load_data_from_log(raw_log)
            # yfinance로 보완 (MA, FI 등 재계산)
            yf_raw = yf.download(ticker, start=str(start_date), end=str(end_date),
                                  auto_adjust=True, progress=False)
            if not yf_raw.empty:
                yf_df = pd.DataFrame({'Close': yf_raw['Close'].squeeze()}).dropna()
                yf_df.index = pd.to_datetime(yf_df.index)
                yf_df['FI_pos'] = (yf_df['Close'] >= yf_df['Close'].shift(1))
                yf_df['MA3'] = calc_ma(yf_df['Close'], 3)
                yf_df['MA5'] = calc_ma(yf_df['Close'], 5)
                yf_df['RSI'] = calc_rsi(yf_df['Close'], DEFAULT['rsi_period'])
                # 모드는 업로드 로그에서
                df_data = df_data[['Mode']].join(yf_df, how='inner')
        else:
            df_data = load_data(ticker, str(start_date), str(end_date),
                                mode_up=mode_up, mode_dn=mode_dn)

    if df_data is None or df_data.empty:
        st.error("데이터를 불러올 수 없습니다. 티커나 날짜를 확인하세요.")
    else:
        with st.spinner("백테스트 실행 중..."):
            asset_df, log_df = run_backtest(df_data, params)
            metrics = calc_metrics(asset_df, float(init_cash))

        st.session_state['asset_df'] = asset_df
        st.session_state['log_df']   = log_df
        st.session_state['metrics']  = metrics
        st.session_state['df_data']  = df_data
        st.session_state['params']   = params

# ── 결과 탭 ──
with tab_result:
    if 'metrics' not in st.session_state:
        st.info("왼쪽 패널에서 파라미터를 설정하고 백테스트를 실행하세요.")
    else:
        m  = st.session_state['metrics']
        ad = st.session_state['asset_df']
        p  = st.session_state['params']

        # 핵심 지표
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("최종 자산",   f"${m['final_asset']:,.0f}")
        c2.metric("누적 수익률", f"{m['total_ret']:+.1f}%")
        c3.metric("CAGR",       f"{m['cagr']:+.1f}%")
        c4.metric("MDD",        f"{m['mdd']:.1f}%")
        c1b,c2b = st.columns(2)
        c1b.metric("Calmar",  f"{m['calmar']:.2f}")
        mode_days = st.session_state['df_data']['Mode'].value_counts()
        c2b.metric("모드 비율", f"공격 {mode_days.get('공격',0)}일 / 방어 {mode_days.get('방어',0)}일")

        # 자산 차트
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                        gridspec_kw={'height_ratios':[3,1]})
        ax1.plot(ad.index, ad['Total_Asset'], color='#1a73e8', linewidth=1.5, label='총 자산')
        ax1.set_ylabel("자산 ($)")
        ax1.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x,_: f'${x:,.0f}'))
        ax1.set_title(f"Dual Sniper Pro 백테스트 — {ticker}")
        ax1.legend()
        ax1.grid(alpha=0.3)

        # 모드 배경색
        dfd = st.session_state['df_data']
        dates = ad.index
        prev_mode = None; start_i = None
        for j, (dt, row) in enumerate(dfd.reindex(dates).iterrows()):
            cur_mode = row.get('Mode','방어')
            if cur_mode != prev_mode:
                if prev_mode is not None and start_i is not None:
                    color = 'rgba(255,100,100,0.08)' if prev_mode == '방어' else 'rgba(100,180,255,0.08)'
                    color = '#ffdddd' if prev_mode == '방어' else '#ddeeff'
                    ax1.axvspan(dates[start_i], dt, alpha=0.15,
                                color='red' if prev_mode=='방어' else 'blue')
                start_i = j
                prev_mode = cur_mode

        # 드로다운
        peak = ad['Total_Asset'].cummax()
        dd   = (ad['Total_Asset'] - peak) / peak * 100
        ax2.fill_between(ad.index, dd, 0, alpha=0.5, color='#d93025', label='Drawdown')
        ax2.set_ylabel("DD (%)")
        ax2.grid(alpha=0.3)
        ax2.legend()
        plt.tight_layout()
        st.pyplot(fig)
        plt.close()

        # 연도별 성과표
        st.subheader("📅 연도별 성과")
        yearly_rows = []
        for yr, yd in sorted(m['yearly'].items()):
            yearly_rows.append({
                '연도': yr,
                '수익률': f"{yd['수익률']:+.1f}%",
                'MDD': f"{yd['MDD']:.1f}%",
                '기말 자산': f"${yd['기말자산']:,.0f}",
            })
        st.dataframe(pd.DataFrame(yearly_rows), hide_index=True, use_container_width=True)

# ── 매매 로그 탭 ──
with tab_log:
    if 'log_df' not in st.session_state or st.session_state['log_df'].empty:
        st.info("백테스트 실행 후 로그가 표시됩니다.")
    else:
        log = st.session_state['log_df'].copy()
        log['날짜'] = pd.to_datetime(log['날짜']).dt.strftime('%Y-%m-%d')
        log['종가'] = log['종가'].apply(lambda x: f"${x:.2f}")
        log['수량'] = log['수량'].apply(lambda x: f"{x:.1f}")
        log['손익'] = log['손익'].apply(lambda x: f"${x:+,.0f}")
        log['잔여현금'] = log['Cash'].apply(lambda x: f"${x:,.0f}")

        # 필터
        col1, col2 = st.columns(2)
        filter_action = col1.multiselect("Action 필터",
            options=log['Action'].unique().tolist(), default=log['Action'].unique().tolist())
        filter_tier = col2.multiselect("티어 필터",
            options=log['티어'].unique().tolist(), default=log['티어'].unique().tolist())

        filtered = log[log['Action'].isin(filter_action) & log['티어'].isin(filter_tier)]
        st.dataframe(filtered[['날짜','Action','티어','종가','수량','손익','사유','잔여현금']],
                     hide_index=True, use_container_width=True)
        buf = io.BytesIO()
        filtered.to_csv(buf, index=False, encoding='utf-8-sig')
        st.download_button("📥 CSV 다운로드", buf.getvalue(),
                           file_name="dual_sniper_log.csv", mime="text/csv")

# ── 로그 비교 탭 ──
with tab_compare:
    st.subheader("📊 원본 로그 vs 백테스트 비교")
    st.info("Dual Sniper 원본 로그 CSV를 업로드하면 연도별 수익률을 비교합니다.")
    orig_file = st.file_uploader("원본 로그 CSV", type="csv", key="compare_upload")
    if orig_file is not None and 'metrics' in st.session_state:
        orig = pd.read_csv(orig_file)
        orig = orig[orig['날짜'].str.match(r'\d{2}-\d{2}-\d{2}', na=False)].copy()
        orig['날짜'] = pd.to_datetime('20' + orig['날짜'].str.split(' ').str[0])
        orig['Total_Asset'] = orig['총자산'].str.replace('[$,]','',regex=True).astype(float)
        orig['year'] = orig['날짜'].dt.year
        orig_yearly = orig.groupby('year')['Total_Asset'].last()

        my_m = st.session_state['metrics']['yearly']
        rows = []
        for yr in sorted(set(orig['year'].unique()) | set(my_m.keys())):
            o_a = orig_yearly.get(yr, None)
            m_a = my_m.get(yr, {}).get('기말자산', None)
            init = float(st.session_state['params']['init_cash'])
            o_r  = (o_a / init - 1) * 100 if o_a and yr == min(orig['year']) else None
            rows.append({'연도': yr,
                         '원본 기말자산': f"${o_a:,.0f}" if o_a else '-',
                         '백테스트 기말자산': f"${m_a:,.0f}" if m_a else '-'})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

# ── 전략 로직 탭 ──
with tab_logic:
    st.markdown("""
## 🎯 Dual Sniper Pro 전략 로직

### 1. 모드 전환
| 항목 | 내용 |
|------|------|
| **공격모드 진입** | 주봉 RSI(wRSI) ≥ 기준값 상향 돌파 |
| **방어모드 진입** | 주봉 RSI(wRSI) < 기준값 하향 돌파 |
| **전환 기준** | 주간 캔들 확정 시점 |

---

### 2. 공격모드 로직

#### 매수
| 조건 | 내용 |
|------|------|
| **FI 음수(-)** | 전일 종가 × (1 + -0.1%) LOC 매수 |
| **FI 양수(+)** | 전일 종가 × (1 + 입력%) LOC 매수 |
| **티어 운용** | 하루 1티어씩, 비어 있는 첫 티어 채움 |
| **할당금** | 총자산 / 분할수 |

#### 보유기간 정규화
```
n = n_min + (n_max - n_min) × (1−x)^(1/α)
x = clamp((매수RSI − 35) / 30, 0, 1)
```
→ RSI 낮을수록(과매도) 더 오래 보유

#### 매도조건 정규화
```
sell% = sell_min + (sell_max − sell_min) × (1−x)^(1/α)
```
→ RSI 낮을수록 더 높은 수익률 목표 (회복 기다림)

#### 매도 보류 조건
전일 종가 > 전일 MA(5) 이면 T1 매도 보류 (MOC 제외)

---

### 3. 방어모드 로직

#### 매수
| 조건 | 내용 |
|------|------|
| **매수기준가** | min(MA(3)×(1+cond1%), 전일종가×(1+cond2%)) |
| **티어 비중** | 6 / 13 / 20 / 27 / 34% (오름 등차수열) |
| **보유기간** | 8일 고정 (calendar day) |

#### 매도
| 조건 | 내용 |
|------|------|
| **MA 매도** | `factor×(C[-1]+C[-2])/(n−factor)` ≥ 오늘 종가 |
| **MOC 청산** | 보유기간 만료 시 무조건 청산 |

---

### 4. 공통 규칙
- MOC 매도가 존재하는 날은 다른 매도 주문 없음
- 각 티어는 독립적으로 운용 (공격 5개, 방어 5개 슬롯)
- 공격/방어 포지션 풀은 별도 관리
""")
