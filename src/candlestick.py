import numpy as np
import pandas as pd
import talib


# All 61 TA-Lib candlestick pattern functions
CANDLESTICK_FUNCTIONS = [
    "CDL2CROWS", "CDL3BLACKCROWS", "CDL3INSIDE", "CDL3LINESTRIKE",
    "CDL3OUTSIDE", "CDL3STARSINSOUTH", "CDL3WHITESOLDIERS",
    "CDLABANDONEDBABY", "CDLADVANCEBLOCK", "CDLBELTHOLD",
    "CDLBREAKAWAY", "CDLCLOSINGMARUBOZU", "CDLCONCEALBABYSWALL",
    "CDLCOUNTERATTACK", "CDLDARKCLOUDCOVER", "CDLDOJI",
    "CDLDOJISTAR", "CDLDRAGONFLYDOJI", "CDLENGULFING",
    "CDLEVENINGDOJISTAR", "CDLEVENINGSTAR", "CDLGAPSIDESIDEWHITE",
    "CDLGRAVESTONEDOJI", "CDLHAMMER", "CDLHANGINGMAN",
    "CDLHARAMI", "CDLHARAMICROSS", "CDLHIGHWAVE",
    "CDLHIKKAKE", "CDLHIKKAKEMOD", "CDLHOMINGPIGEON",
    "CDLIDENTICAL3CROWS", "CDLINNECK", "CDLINVERTEDHAMMER",
    "CDLKICKING", "CDLKICKINGBYLENGTH", "CDLLADDERBOTTOM",
    "CDLLONGLEGGEDDOJI", "CDLLONGLINE", "CDLMARUBOZU",
    "CDLMATCHINGLOW", "CDLMATHOLD", "CDLMORNINGDOJISTAR",
    "CDLMORNINGSTAR", "CDLONNECK", "CDLPIERCING",
    "CDLRICKSHAWMAN", "CDLRISEFALL3METHODS", "CDLSEPARATINGLINES",
    "CDLSHOOTINGSTAR", "CDLSHORTLINE", "CDLSPINNINGTOP",
    "CDLSTALLEDPATTERN", "CDLSTICKSANDWICH", "CDLTAKURI",
    "CDLTASUKIGAP", "CDLTHRUSTING", "CDLTRISTAR",
    "CDLUNIQUE3RIVER", "CDLUPSIDEGAP2CROWS", "CDLXSIDEGAP3METHODS",
]


def scan_candlestick_patterns(df: pd.DataFrame) -> list[dict]:
    """Scan a ticker for all 61 TA-Lib candlestick patterns.

    Returns list of dicts: {date, pattern, direction, strength}
    """
    o = df["Open"].values.astype(np.float64)
    h = df["High"].values.astype(np.float64)
    l = df["Low"].values.astype(np.float64)
    c = df["Close"].values.astype(np.float64)
    dates = df.index

    results = []
    for func_name in CANDLESTICK_FUNCTIONS:
        func = getattr(talib, func_name)
        signals = func(o, h, l, c)

        for i in np.nonzero(signals)[0]:
            val = int(signals[i])
            results.append({
                "date": str(dates[i]),
                "pattern": func_name.replace("CDL", ""),
                "direction": "bullish" if val > 0 else "bearish",
                "strength": abs(val),  # 100 = normal, 200 = strong
            })

    results.sort(key=lambda x: x["date"])
    return results
