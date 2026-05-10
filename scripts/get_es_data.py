import databento as db

client = db.Historical("db-5QKT5u4esGw6LWamgryBEGjPM9nGf")

data = client.timeseries.get_range(
    dataset="GLBX.MDP3",
    schema="ohlcv-1m",
    symbols=["ES.n.0"],  # Correct continuous contract symbol
    stype_in="continuous",
    start="2010-06-06",
    end="2026-04-23",
)

data.to_csv(r"C:\Users\conor\iCloudDrive\Trading\quant-research\01_data\raw\ES_1min.csv")

print("Download complete!")