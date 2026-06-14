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
    df: Close, RSI, FI_pos, MA3, MA5, wRSI, Mode 컬럼 포함
    p : 파라미터 dict
    반환: asset_df, log_df (1일 1행, 30컬럼)
    """
    init_cash   = p['init_cash']
    atk_tiers   = p['atk_tiers']
    def_tiers   = p['def_tiers']
    def_weights = [w / 100 for w in p['def_weights']]

    dates  = df.index.tolist()
    n_days = len(dates)
    closes = df['Close'].values
    rsis   = df['RSI'].values
    fi_pos = df['FI_pos'].values
    ma3    = df['MA3'].values
    ma5    = df['MA5'].values
    wrsi_v = df['wRSI'].values if 'wRSI' in df.columns else np.full(n_days, np.nan)
    modes  = df['Mode'].values

    cash       = init_cash
    daily_log  = []
    asset_recs = []

    atk_slots = [None] * atk_tiers
    def_slots = [None] * def_tiers

    peak_asset = init_cash
    prev_total = init_cash

    for i, date in enumerate(dates):
        close  = float(closes[i])
        rsi    = float(rsis[i])
        mode   = modes[i]
        fi_p   = bool(fi_pos[i])
        _ma3   = float(ma3[i])
        _ma5   = float(ma5[i])
        _wrsi  = float(wrsi_v[i]) if not np.isnan(wrsi_v[i]) else None
        prev_close  = float(closes[i-1]) if i > 0 else close
        prev2_close = float(closes[i-2]) if i > 1 else prev_close

        daily_ret = (close / prev_close - 1) * 100 if i > 0 else 0.0
        fi_str    = '=+' if fi_p else '-'

        # ─── 방어 MA 매도 트리거 (항상 계산) ───
        factor = 1 + p['def_sell_pct'] / 100
        if not (np.isnan(prev_close) or np.isnan(prev2_close)):
            def_sell_trig = factor * (prev_close + prev2_close) / (p['def_ma_period'] - factor)
        else:
            def_sell_trig = np.nan

        # ─── 매도 처리 ───
        sells_today = []   # {tier, shares, buy_price, sell_price, pnl, reason, sell_cond, sell_pct}

        # 공격 슬롯
        if mode == '공격':
            for ti in range(atk_tiers):
                s = atk_slots[ti]
                if s is None:
                    continue
                if s['mode'] != '공격':
                    pnl = (close - s['buy_price']) * s['shares']
                    cash += s['shares'] * close
                    sells_today.append({'tier': f'공T{ti+1}', 'shares': s['shares'],
                                        'pnl': pnl, 'reason': '모드청산',
                                        'sell_cond': close, 'sell_pct': 0})
                    atk_slots[ti] = None
                    continue
                if i >= s['hold_until_idx']:
                    pnl = (close - s['buy_price']) * s['shares']
                    cash += s['shares'] * close
                    sells_today.append({'tier': f'공T{ti+1}', 'shares': s['shares'],
                                        'pnl': pnl, 'reason': 'MOC',
                                        'sell_cond': close, 'sell_pct': s.get('sell_pct', 0)})
                    atk_slots[ti] = None
                    continue
                if close >= s['sell_target']:
                    if ti == 0 and i > 0 and closes[i-1] > (ma5[i-1] if not np.isnan(ma5[i-1]) else 0):
                        pass  # MA5 보류
                    else:
                        pnl = (close - s['buy_price']) * s['shares']
                        cash += s['shares'] * close
                        sells_today.append({'tier': f'공T{ti+1}', 'shares': s['shares'],
                                            'pnl': pnl, 'reason': f'{s.get("sell_pct",0):.2f}%',
                                            'sell_cond': s['sell_target'],
                                            'sell_pct': s.get('sell_pct', 0)})
                        atk_slots[ti] = None

        # 방어 슬롯 (모드 무관)
        for ti in range(def_tiers):
            s = def_slots[ti]
            if s is None:
                continue
            if i >= s['hold_until_idx']:
                pnl = (close - s['buy_price']) * s['shares']
                cash += s['shares'] * close
                sells_today.append({'tier': f'방T{ti+1}', 'shares': s['shares'],
                                    'pnl': pnl, 'reason': 'MOC',
                                    'sell_cond': close, 'sell_pct': 0})
                def_slots[ti] = None
                continue
            if not np.isnan(def_sell_trig) and close >= def_sell_trig:
                pnl = (close - s['buy_price']) * s['shares']
                cash += s['shares'] * close
                sells_today.append({'tier': f'방T{ti+1}', 'shares': s['shares'],
                                    'pnl': pnl, 'reason': 'MA',
                                    'sell_cond': def_sell_trig, 'sell_pct': 0})
                def_slots[ti] = None

        # ─── 매수 처리 ───
        hv_after_sell  = sum(s['shares'] * close for s in atk_slots + def_slots if s is not None)
        total_after_sell = cash + hv_after_sell

        buy_today         = None
        buy_trigger_price = None
        buy_alloc_shown   = None

        if mode == '공격':
            buy_cond_pct  = p['atk_fi_neg'] if not fi_p else p['atk_buy_pct']
            buy_trigger_price = prev_close * (1 + buy_cond_pct / 100)
            next_empty = next((ti for ti in range(atk_tiers) if atk_slots[ti] is None), None)
            if next_empty is not None:
                buy_alloc_shown = total_after_sell / atk_tiers
                if close <= buy_trigger_price:
                    alloc = min(total_after_sell / atk_tiers, cash)
                    if alloc >= 1:
                        shares = alloc / close
                        hold_d = calc_hold_days(rsi, p['atk_hold_nmin'], p['atk_hold_nmax'], p['atk_hold_a'])
                        sell_p = calc_sell_pct(rsi, p['atk_sell_min'], p['atk_sell_max'], p['atk_sell_a'])
                        sell_t = close * (1 + sell_p / 100)
                        cash  -= alloc
                        atk_slots[next_empty] = {
                            'buy_price': close, 'sell_target': sell_t,
                            'hold_until_idx': i + hold_d, 'shares': shares,
                            'mode': '공격', 'rsi': rsi, 'hold_days': hold_d, 'sell_pct': sell_p,
                        }
                        buy_today = {
                            'tier': f'공T{next_empty+1}',
                            'trigger': buy_trigger_price,
                            'alloc': alloc,
                            'alloc_pct': alloc / total_after_sell * 100,
                            'actual_amt': alloc,
                            'shares': shares,
                            'hold_days': hold_d,
                            'sell_pct': sell_p,
                        }

        else:  # 방어
            if not np.isnan(_ma3):
                cond1 = _ma3 * (1 + p['def_buy_ma'] / 100)
                cond2 = prev_close * (1 + p['def_buy_prev'] / 100)
                buy_trigger_price = min(cond1, cond2)
                next_empty = next((ti for ti in range(def_tiers) if def_slots[ti] is None), None)
                if next_empty is not None:
                    w = def_weights[next_empty]
                    buy_alloc_shown = total_after_sell * w
                    if close <= buy_trigger_price:
                        alloc = min(total_after_sell * w, cash)
                        if alloc >= 1:
                            shares = alloc / close
                            cash  -= alloc
                            def_slots[next_empty] = {
                                'buy_price': close, 'sell_target': None,
                                'hold_until_idx': i + p['def_hold'], 'shares': shares,
                                'mode': '방어', 'rsi': rsi, 'hold_days': p['def_hold'],
                            }
                            buy_today = {
                                'tier': f'방T{next_empty+1}',
                                'trigger': buy_trigger_price,
                                'alloc': alloc,
                                'alloc_pct': alloc / total_after_sell * 100,
                                'actual_amt': alloc,
                                'shares': shares,
                                'hold_days': p['def_hold'],
                                'sell_pct': p['def_sell_pct'],
                            }

        # ─── 종료 포트폴리오 상태 ───
        holdings_val = sum(s['shares'] * close for s in atk_slots + def_slots if s is not None)
        total_shares = sum(s['shares'] for s in atk_slots + def_slots if s is not None)
        total_val    = cash + holdings_val
        peak_asset   = max(peak_asset, total_val)
        dd_pct       = (total_val / peak_asset - 1) * 100
        cum_ret      = (total_val / init_cash - 1) * 100
        asset_chg    = (total_val / prev_total - 1) * 100 if prev_total > 0 else 0.0
        cash_pct     = cash / total_val * 100 if total_val > 0 else 100.0
        prev_total   = total_val

        # ─── 매도 집계 ───
        if sells_today:
            total_sell_amt    = sum(s['sell_cond'] * s['shares'] for s in sells_today)
            total_sell_shares = sum(s['shares'] for s in sells_today)
            total_pnl         = sum(s['pnl'] for s in sells_today)
            reasons_unique    = list(dict.fromkeys(s['reason'] for s in sells_today))
            sell_reason_str   = ','.join(reasons_unique)
            first_cond        = sells_today[0]['sell_cond']
            sell_cond_str = (f"${first_cond:.2f} 외 {len(sells_today)-1}건"
                             if len(sells_today) > 1 else f"${first_cond:.2f}")
        else:
            total_sell_amt = total_sell_shares = total_pnl = None
            sell_reason_str = sell_cond_str = None

        # ─── 매도조건 표시 (매도 없을 때도 이론값 표시) ───
        if sell_cond_str:
            disp_sell_cond = sell_cond_str
        elif mode == '방어' and not np.isnan(def_sell_trig) and any(s is not None for s in def_slots):
            disp_sell_cond = f"${def_sell_trig:.2f}"
        elif mode == '공격':
            atk_targets = [s['sell_target'] for s in atk_slots if s is not None]
            if atk_targets:
                disp_sell_cond = (f"${atk_targets[0]:.2f} 외 {len(atk_targets)-1}건"
                                  if len(atk_targets) > 1 else f"${atk_targets[0]:.2f}")
            else:
                disp_sell_cond = None
        else:
            disp_sell_cond = None

        # ─── 매도% 표시 ───
        if mode == '방어':
            disp_sell_pct = f"{p['def_sell_pct']:.2f}%"
        else:
            if not np.isnan(rsi):
                sp = calc_sell_pct(rsi, p['atk_sell_min'], p['atk_sell_max'], p['atk_sell_a'])
                disp_sell_pct = f"{sp:.2f}%"
            else:
                disp_sell_pct = None

        # ─── 행 빌드 ───
        date_str = date.strftime('%y-%m-%d %a') if hasattr(date, 'strftime') else str(date)
        row = {
            '날짜':       date_str,
            '종가':       round(close, 2),
            '등락':       f"{daily_ret:+.2f}%",
            'MA(n)':      round(_ma3, 2) if not np.isnan(_ma3) else None,
            'MA(5)':      round(_ma5, 2) if not np.isnan(_ma5) else None,
            'wRSI':       round(_wrsi, 1) if _wrsi is not None else '-',
            '일RSI':      round(rsi, 1)   if not np.isnan(rsi) else None,
            '모드':       mode,
            'FI':         fi_str,
            '매도%':      disp_sell_pct,
            '매수조건':   f"${buy_trigger_price:.2f}" if buy_trigger_price is not None else None,
            '매수할당':   f"${buy_alloc_shown:,.0f}"  if buy_alloc_shown  is not None else None,
            '실매수비중': f"{buy_today['alloc_pct']:.0f}%" if buy_today else None,
            '매수티어':   buy_today['tier']            if buy_today else None,
            '실매수금':   round(buy_today['actual_amt'], 2) if buy_today else None,
            '매수량':     round(buy_today['shares'], 1)     if buy_today else None,
            '보유':       buy_today['hold_days']            if buy_today else None,
            '매도조건':   disp_sell_cond,
            '매도사유':   sell_reason_str,
            '실매도금':   round(total_sell_amt,    2) if total_sell_amt    is not None else None,
            '매도량':     round(total_sell_shares, 1) if total_sell_shares is not None else None,
            '손익':       round(total_pnl,         2) if total_pnl         is not None else None,
            '보유수량':   round(total_shares, 1),
            '평가금':     round(holdings_val, 0),
            '총자산':     round(total_val, 0),
            '자산변동':   f"{asset_chg:+.2f}%",
            '누적수익':   f"{cum_ret:+.2f}%",
            'DD':         f"{dd_pct:.2f}%",
            '현금':       round(cash, 0),
            '현금비중':   f"{cash_pct:.1f}%",
        }
        daily_log.append(row)
        asset_recs.append({'날짜': date, 'Total_Asset': total_val, 'Cash': cash, 'Mode': mode})

    asset_df = pd.DataFrame(asset_recs).set_index('날짜')
    log_df   = pd.DataFrame(daily_log) if daily_log else pd.DataFrame()
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
        st.caption("주봉 RSI(wRSI) 기반 자동 모드 전환")
        col1, col2 = st.columns(2)
        mode_up = col1.number_input("공격 진입(wRSI↑)", value=DEFAULT['mode_rsi_up'], step=1.0,
                                    help="wRSI가 이 값 이상 올라오면 공격모드 전환")
        mode_dn = col2.number_input("방어 진입(wRSI↓)", value=DEFAULT['mode_rsi_dn'], step=1.0,
                                    help="wRSI가 이 값 아래로 내려가면 방어모드 전환")

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

    with st.spinner("yfinance 데이터 로드 중..."):
        df_data = load_data(ticker, str(start_date), str(end_date),
                            mode_up=float(mode_up), mode_dn=float(mode_dn))

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

        # 모드 배경색 (공격=파랑, 방어=빨강)
        dfd  = st.session_state['df_data']
        mode_series = dfd.reindex(ad.index)['Mode'].fillna('방어')
        in_block = False; block_start = None; block_mode = None
        for dt, m_val in mode_series.items():
            if not in_block:
                in_block = True; block_start = dt; block_mode = m_val
            elif m_val != block_mode:
                ax1.axvspan(block_start, dt, alpha=0.12,
                            color='steelblue' if block_mode=='공격' else 'tomato')
                block_start = dt; block_mode = m_val
        if in_block:
            ax1.axvspan(block_start, ad.index[-1], alpha=0.12,
                        color='steelblue' if block_mode=='공격' else 'tomato')
        from matplotlib.patches import Patch
        ax1.legend(handles=[
            ax1.lines[0],
            Patch(color='steelblue', alpha=0.4, label='공격모드'),
            Patch(color='tomato',    alpha=0.4, label='방어모드'),
        ], labels=['총 자산','공격모드','방어모드'])

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

        # ── 필터 ──
        st.markdown("##### 🔍 필터")
        fc1, fc2, fc3 = st.columns(3)
        mode_opts  = log['모드'].unique().tolist()
        mode_sel   = fc1.multiselect("모드", mode_opts, default=mode_opts)
        show_buy   = fc2.checkbox("매수일만 보기", False)
        show_sell  = fc3.checkbox("매도일만 보기", False)

        mask = log['모드'].isin(mode_sel)
        if show_buy:
            mask &= log['매수티어'].notna()
        if show_sell:
            mask &= log['매도사유'].notna()
        filtered = log[mask].copy()

        # ── 컬럼 그룹 선택 ──
        st.markdown("##### 📋 표시 컬럼 그룹")
        gc1, gc2, gc3, gc4 = st.columns(4)
        grp_basic  = gc1.checkbox("기본 지표",   True)
        grp_buy    = gc2.checkbox("매수 정보",   True)
        grp_sell   = gc3.checkbox("매도 정보",   True)
        grp_port   = gc4.checkbox("포트폴리오",  True)

        show_cols = []
        if grp_basic:
            show_cols += ['날짜','종가','등락','MA(n)','MA(5)','wRSI','일RSI','모드','FI','매도%']
        if grp_buy:
            show_cols += ['매수조건','매수할당','실매수비중','매수티어','실매수금','매수량','보유']
        if grp_sell:
            show_cols += ['매도조건','매도사유','실매도금','매도량','손익']
        if grp_port:
            show_cols += ['보유수량','평가금','총자산','자산변동','누적수익','DD','현금','현금비중']
        # 날짜는 항상 포함
        if '날짜' not in show_cols:
            show_cols = ['날짜'] + show_cols

        available = [c for c in show_cols if c in filtered.columns]

        # ── 통계 요약 ──
        n_buy_days  = filtered['매수티어'].notna().sum()
        n_sell_days = filtered['매도사유'].notna().sum()
        n_pnl_pos   = (filtered['손익'].dropna() > 0).sum()
        n_pnl_neg   = (filtered['손익'].dropna() < 0).sum()
        total_pnl   = filtered['손익'].dropna().sum()

        ms1, ms2, ms3, ms4 = st.columns(4)
        ms1.metric("매수일 수",   f"{n_buy_days}일")
        ms2.metric("매도일 수",   f"{n_sell_days}일")
        ms3.metric("수익/손실 매도", f"{n_pnl_pos}건 / {n_pnl_neg}건")
        ms4.metric("총 실현손익", f"${total_pnl:,.0f}")

        st.dataframe(
            filtered[available],
            hide_index=True,
            use_container_width=True,
            height=500,
            column_config={
                '날짜':    st.column_config.TextColumn('날짜',     width=110),
                '종가':    st.column_config.NumberColumn('종가',   format="$%.2f"),
                '실매수금':st.column_config.NumberColumn('실매수금',format="$%.2f"),
                '실매도금':st.column_config.NumberColumn('실매도금',format="$%.2f"),
                '손익':    st.column_config.NumberColumn('손익',   format="$%.2f"),
                '평가금':  st.column_config.NumberColumn('평가금', format="$%.0f"),
                '총자산':  st.column_config.NumberColumn('총자산', format="$%.0f"),
                '현금':    st.column_config.NumberColumn('현금',   format="$%.0f"),
            }
        )

        # ── 다운로드 ──
        buf = io.BytesIO()
        log.to_csv(buf, index=False, encoding='utf-8-sig')
        st.download_button("📥 전체 로그 CSV 다운로드", buf.getvalue(),
                           file_name="dual_sniper_log.csv", mime="text/csv")

# ── 로그 비교 탭 ──
with tab_compare:
    st.subheader("📊 원본 로그 vs 백테스트 비교")
    st.info("Dual Sniper 원본 로그 CSV를 업로드하면 연도별 자산·수익률을 비교합니다.")
    orig_file = st.file_uploader("원본 로그 CSV", type="csv", key="compare_upload")
    if orig_file is not None:
        import re as _re
        orig = pd.read_csv(orig_file)
        orig = orig[orig['날짜'].astype(str).str.match(r'\d{2}-\d{2}-\d{2}', na=False)].copy()
        orig['날짜'] = pd.to_datetime('20' + orig['날짜'].str.split(' ').str[0])
        orig['Total_Asset'] = orig['총자산'].apply(
            lambda x: float(_re.sub(r'[$,]','',str(x))) if pd.notna(x) else np.nan)
        orig['year'] = orig['날짜'].dt.year
        orig_yearly = orig.groupby('year')['Total_Asset'].last()

        # 원본 연도별 수익률 계산
        def yearly_ret(asset_series):
            rets = {}
            prev = None
            for yr in sorted(asset_series.index):
                cur = asset_series[yr]
                if prev is None:
                    rets[yr] = None
                else:
                    rets[yr] = (cur / prev - 1) * 100
                prev = cur
            return rets
        orig_ret = yearly_ret(orig_yearly)

        if 'metrics' in st.session_state:
            my_m = st.session_state['metrics']['yearly']
            init = float(st.session_state['params']['init_cash'])
            rows = []
            all_years = sorted(set(orig_yearly.index) | set(my_m.keys()))
            for yr in all_years:
                o_a = orig_yearly.get(yr)
                m_a = my_m.get(yr, {}).get('기말자산')
                o_r = orig_ret.get(yr)
                m_r = my_m.get(yr, {}).get('수익률')
                diff = (m_r - o_r) if (m_r is not None and o_r is not None) else None
                rows.append({
                    '연도': yr,
                    '원본 기말자산':    f"${o_a:,.0f}"   if o_a   else '-',
                    '백테스트 기말자산': f"${m_a:,.0f}"  if m_a   else '-',
                    '원본 수익률':      f"{o_r:+.1f}%"   if o_r is not None else '-',
                    '백테스트 수익률':  f"{m_r:+.1f}%"   if m_r is not None else '-',
                    '차이':            f"{diff:+.1f}%p"  if diff is not None else '-',
                })
            cmp_df = pd.DataFrame(rows)
            st.dataframe(cmp_df, hide_index=True, use_container_width=True)

            # 자산 비교 차트
            fig2, ax = plt.subplots(figsize=(12,4))
            ax.plot(sorted(orig_yearly.index),
                    [orig_yearly[y] for y in sorted(orig_yearly.index)],
                    marker='o', label='원본', color='#e8710a')
            ax.plot(sorted(my_m.keys()),
                    [my_m[y]['기말자산'] for y in sorted(my_m.keys())],
                    marker='s', label='백테스트', color='#1a73e8', linestyle='--')
            ax.set_title("연도별 기말 자산 비교")
            ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x,_: f'${x:,.0f}'))
            ax.legend(); ax.grid(alpha=0.3)
            plt.tight_layout()
            st.pyplot(fig2); plt.close()
        else:
            st.warning("먼저 백테스트를 실행하세요.")

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
