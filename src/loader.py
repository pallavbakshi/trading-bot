from pathlib import Path
import pandas as pd


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "Volume": "Volume"})
    df = df[["date", "Open", "High", "Low", "Close", "Volume"]]
    df = df.set_index("date")
    df = df.sort_index()
    return df


def load_all(data_dir: str | Path = "data") -> dict[str, pd.DataFrame]:
    data_dir = Path(data_dir)
    tickers = {}
    for csv_path in sorted(data_dir.glob("*.csv")):
        ticker = csv_path.stem.upper()
        tickers[ticker] = load_csv(csv_path)
    return tickers
