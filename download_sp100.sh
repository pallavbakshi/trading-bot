#!/bin/bash

# S&P 100 tickers (excluding NVDA and AAPL, already downloaded)
tickers=(
  MSFT AMZN GOOGL GOOG META AVGO TSLA BRK.B WMT LLY
  JPM XOM V JNJ MA ORCL MU COST NFLX ABBV
  CVX PLTR HD BAC PG GE KO CAT AMD CSCO
  MRK AMAT RTX LRCX PM UNH MS WFC GS TMUS
  IBM MCD INTC LIN GEV PEP VZ AXP T AMGN
  TMO ABT C NEE CRM KLAC DIS GILD TXN TJX
  ISRG ANET BA APP SCHW APH ADI DE BLK UBER
  UNP HON PFE LMT QCOM BKNG WELL DHR LOW COP
  SYK ETN SPGI PANW ACN INTU CB PLD NOW NEM
  BMY PGR PH COF CEG MDT HCA VRTX
)

for ticker in "${tickers[@]}"; do
  echo "=== Downloading $ticker ==="
  tv symbol "$ticker"
  tv goto 2010-01-01 --to 2026-03-05
  tv download -o "data/sp/$(echo "$ticker" | tr '[:upper:]' '[:lower:]').csv"
  echo "=== Done: $ticker ==="
  echo ""
  sleep 5
done

echo "All downloads complete!"
