// FortressBarWriter.cs
// =====================================================================
// NinjaTrader 8 Indicator — streams OHLCV + CVD + DOM bars to the VPS
// via TCP, and optionally also writes JSONL files locally.
//
// HOW TO INSTALL:
//   1. NinjaTrader 8 → Tools → NinjaScript Editor
//   2. Right-click Indicators → New → name it exactly: FortressBarWriter
//   3. Delete all default code, paste this entire file
//   4. Press F5 to compile. Fix any errors in the Output window.
//   5. Close the editor.
//
// HOW TO USE:
//   1. Open one chart per instrument:
//        MGC 09-26 (or @MGC continuous)  → GC
//        MNQ 09-26 (or @MNQ)             → NQ
//        MES 09-26 (or @MES)             → ES
//        SIL 09-26 (or @SIL)             → SI
//   2. For each chart: Right-click → Indicators → Add → FortressBarWriter
//   3. Set bar period to 1 Minute (repeat for 3, 5, 15, 30 min as needed).
//      One indicator instance per timeframe per instrument.
//   4. Settings panel:
//        VPS Host     = your VPS IP address (e.g. 123.45.67.89)
//        VPS Port     = 9876  (must match tick_nt8_bridge_server.py)
//        Output Dir   = leave default (local file backup)
//        Write Files  = True  (local JSONL backup, set False to save disk)
//   5. Click OK. The status panel on the chart shows connection state.
//
// DATA FLOW:
//   NT8 bar closes → FortressBarWriter → TCP → VPS:9876
//                                      → JSONL file (local backup)
//   VPS tick_nt8_bridge_server.py receives bars → updates parquets
//   VPS tick_live_executor.py reads updated parquets → trades signals
//
// WHAT EACH BAR CONTAINS:
//   OHLCV + buy_vol + sell_vol + cvd_delta + cvd (running) +
//   DOM 5 levels bid/ask + OBI-5 + microprice + spread
//   All the data the V1-V10 strategies need to generate real signals.
// =====================================================================

#region Using declarations
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class FortressBarWriter : Indicator
    {
        // ── DOM snapshot (updated by OnMarketDepth) ───────────────────────────
        private readonly double[] _bidPx = new double[10];
        private readonly double[] _askPx = new double[10];
        private readonly double[] _bidSz = new double[10];
        private readonly double[] _askSz = new double[10];

        // ── CVD tracking ──────────────────────────────────────────────────────
        private double _buyVol;
        private double _sellVol;
        private double _cumCvd;
        private double _prevClose;
        private bool   _isFirstBar;

        // ── File output ───────────────────────────────────────────────────────
        private StreamWriter _writer;
        private string       _filePath;
        private string       _baseSymbol;
        private string       _barMinStr;
        private double       _tickSize;

        // ── TCP sender ────────────────────────────────────────────────────────
        private Thread              _senderThread;
        private ConcurrentQueue<string> _sendQueue;
        private volatile bool       _stopSender;
        private volatile bool       _tcpConnected;
        private string              _statusLine;

        #region OnStateChange
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Fortress Bar Writer — real-time OHLCV+CVD+DOM to VPS";
                Name        = "FortressBarWriter";
                Calculate   = Calculate.OnEachTick;
                IsOverlay   = true;
                DisplayInDataBox         = false;
                IsSuspendedWhileInactive = false;

                VpsHost   = "";   // Set to your VPS IP. Empty = file-only mode.
                VpsPort   = 9876;
                WriteFiles = true;
                OutputDir  = System.IO.Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    "AppData", "Local", "fortress_live"
                );
            }
            else if (State == State.DataLoaded)
            {
                _tickSize  = Instrument.MasterInstrument.TickSize;
                _baseSymbol = ExtractBaseSymbol(Instrument.MasterInstrument.Name);
                _barMinStr  = BarsPeriod.Value.ToString();
                _isFirstBar = true;
                _prevClose  = 0;
                _buyVol     = 0;
                _sellVol    = 0;
                _cumCvd     = 0;
                for (int i = 0; i < 10; i++)
                { _bidPx[i]=0; _bidSz[i]=0; _askPx[i]=0; _askSz[i]=0; }

                // File output
                if (WriteFiles)
                {
                    try
                    {
                        Directory.CreateDirectory(OutputDir);
                        _filePath = Path.Combine(OutputDir,
                            $"{_baseSymbol}_{_barMinStr}m_live.jsonl");
                        _writer = new StreamWriter(_filePath, append: true, encoding: Encoding.UTF8);
                        _writer.AutoFlush = true;
                        Print($"[Fortress] {_baseSymbol} {_barMinStr}m file: {_filePath}");
                    }
                    catch (Exception ex)
                    {
                        Print($"[Fortress] File open error: {ex.Message}");
                    }
                }

                // TCP sender thread
                if (!string.IsNullOrWhiteSpace(VpsHost))
                {
                    _sendQueue  = new ConcurrentQueue<string>();
                    _stopSender = false;
                    _senderThread = new Thread(SenderLoop)
                    {
                        IsBackground = true,
                        Name = $"FBW-{_baseSymbol}-{_barMinStr}m"
                    };
                    _senderThread.Start();
                    Print($"[Fortress] {_baseSymbol} {_barMinStr}m TCP -> {VpsHost}:{VpsPort}");
                }
                else
                {
                    Print($"[Fortress] {_baseSymbol} {_barMinStr}m file-only (no VPS host set)");
                }

                _statusLine = $"Fortress {_baseSymbol} {_barMinStr}m";
            }
            else if (State == State.Terminated)
            {
                _stopSender = true;
                CloseWriter();
            }
        }
        #endregion

        #region OnBarUpdate
        protected override void OnBarUpdate()
        {
            if (IsFirstTickOfBar && !_isFirstBar && CurrentBar > 0)
            {
                string json = BuildBarJson();
                // Write to file
                try { _writer?.WriteLine(json); } catch { }
                // Enqueue for TCP send
                _sendQueue?.Enqueue(json);
                _buyVol  = 0;
                _sellVol = 0;
            }
            _isFirstBar = false;
            if (IsFirstTickOfBar) return;

            double px  = Close[0];
            double vol = Volume[0];
            double ask0 = _askPx[0];
            double bid0 = _bidPx[0];

            if (ask0 > 0 && px >= ask0 - _tickSize * 0.5)
                _buyVol += vol;
            else if (bid0 > 0 && px <= bid0 + _tickSize * 0.5)
                _sellVol += vol;
            else if (_prevClose > 0)
            {
                if (px >= _prevClose) _buyVol += vol;
                else                  _sellVol += vol;
            }
            _prevClose = px;
        }
        #endregion

        #region OnMarketDepth
        protected override void OnMarketDepth(MarketDepthEventArgs e)
        {
            int pos = e.Position;
            if (pos < 0 || pos >= 10) return;
            if (e.MarketDataType == MarketDataType.Ask)
            { _askPx[pos] = e.Price; _askSz[pos] = e.Volume; }
            else if (e.MarketDataType == MarketDataType.Bid)
            { _bidPx[pos] = e.Price; _bidSz[pos] = e.Volume; }
        }
        #endregion

        #region OnRender (status overlay on chart)
        protected override void OnRender(NinjaTrader.Gui.Chart.ChartControl cc,
                                         NinjaTrader.Gui.Chart.ChartScale cs)
        {
            if (string.IsNullOrWhiteSpace(VpsHost)) return;
            string state = _tcpConnected ? "TCP OK" : "TCP DOWN";
            string txt   = $"{_statusLine} | {state}";
            // Draw in top-left corner using NinjaTrader's built-in Draw.TextFixed
            Draw.TextFixed(this, "fw_status", txt,
                NinjaTrader.Gui.Chart.TextPosition.TopLeft);
        }
        #endregion

        #region TCP Sender Thread
        private void SenderLoop()
        {
            while (!_stopSender)
            {
                TcpClient client = null;
                NetworkStream stream = null;
                try
                {
                    client = new TcpClient();
                    client.Connect(VpsHost, VpsPort);
                    client.NoDelay = true;
                    stream = client.GetStream();
                    _tcpConnected = true;
                    Print($"[Fortress] {_baseSymbol} {_barMinStr}m connected to {VpsHost}:{VpsPort}");

                    // Send handshake
                    string handshake = $"{{\"type\":\"hello\",\"sym\":\"{_baseSymbol}\",\"bar_min\":\"{_barMinStr}\"}}\n";
                    byte[] hbytes = Encoding.UTF8.GetBytes(handshake);
                    stream.Write(hbytes, 0, hbytes.Length);

                    while (!_stopSender)
                    {
                        // Drain the queue
                        while (_sendQueue.TryDequeue(out string json))
                        {
                            byte[] data = Encoding.UTF8.GetBytes(json + "\n");
                            stream.Write(data, 0, data.Length);
                        }
                        // Heartbeat every 30s
                        Thread.Sleep(200);
                    }
                }
                catch (Exception ex)
                {
                    _tcpConnected = false;
                    if (!_stopSender)
                    {
                        Print($"[Fortress] {_baseSymbol} {_barMinStr}m TCP error: {ex.Message}. Reconnecting in 10s...");
                        Thread.Sleep(10000);
                    }
                }
                finally
                {
                    _tcpConnected = false;
                    try { stream?.Close(); } catch { }
                    try { client?.Close(); } catch { }
                }
            }
        }
        #endregion

        #region BuildBarJson
        private string BuildBarJson()
        {
            _cumCvd += (_buyVol - _sellVol);

            double bid0   = _bidPx[0];
            double ask0   = _askPx[0];
            double spread = (ask0 > 0 && bid0 > 0) ? ask0 - bid0 : 0;

            double bSz1 = _bidSz[0];
            double aSz1 = _askSz[0];
            double tot1 = bSz1 + aSz1;
            double bp   = (tot1 > 0) ? (bSz1 - aSz1) / tot1 : 0;

            double bTot5 = 0, aTot5 = 0;
            for (int i = 0; i < 5; i++) { bTot5 += _bidSz[i]; aTot5 += _askSz[i]; }
            double tot5 = bTot5 + aTot5;
            double obi5 = (tot5 > 0) ? (bTot5 - aTot5) / tot5 : 0;

            double micro = (tot1 > 0) ? (bid0 * aSz1 + ask0 * bSz1) / tot1
                                      : (bid0 + ask0) / 2.0;

            var sb = new StringBuilder();
            sb.Append("{");
            sb.AppendFormat("\"type\":\"bar\",");
            sb.AppendFormat("\"sym\":\"{0}\",",   _baseSymbol);
            sb.AppendFormat("\"bar_min\":\"{0}\",", _barMinStr);
            sb.AppendFormat("\"ts\":\"{0:yyyy-MM-ddTHH:mm:ss}Z\",", Time[1].ToUniversalTime());
            sb.AppendFormat("\"open\":{0},",          F(Open[1]));
            sb.AppendFormat("\"high\":{0},",          F(High[1]));
            sb.AppendFormat("\"low\":{0},",           F(Low[1]));
            sb.AppendFormat("\"close\":{0},",         F(Close[1]));
            sb.AppendFormat("\"volume\":{0},",        (long)Volume[1]);
            sb.AppendFormat("\"buy_vol\":{0},",       (long)_buyVol);
            sb.AppendFormat("\"sell_vol\":{0},",      (long)_sellVol);
            sb.AppendFormat("\"cvd_delta\":{0},",     (long)(_buyVol - _sellVol));
            sb.AppendFormat("\"cvd\":{0},",           (long)_cumCvd);
            sb.AppendFormat("\"spread\":{0},",        F(spread));
            sb.AppendFormat("\"bid_sz_00\":{0},",     (long)bSz1);
            sb.AppendFormat("\"ask_sz_00\":{0},",     (long)aSz1);
            sb.AppendFormat("\"book_pressure\":{0},", F(bp));
            sb.AppendFormat("\"obi_5\":{0},",         F(obi5));
            sb.AppendFormat("\"microprice\":{0},",    F(micro));
            for (int i = 0; i < 5; i++)
            {
                sb.AppendFormat("\"bid_px_{0:D2}\":{1},", i, F(_bidPx[i]));
                sb.AppendFormat("\"ask_px_{0:D2}\":{1},", i, F(_askPx[i]));
                sb.AppendFormat("\"bid_sz_{0:D2}\":{1},", i, (long)_bidSz[i]);
                sb.AppendFormat("\"ask_sz_{0:D2}\":{1},", i, (long)_askSz[i]);
            }
            sb.AppendFormat("\"n_trades\":{0}", (long)(_buyVol + _sellVol));
            sb.Append("}");
            return sb.ToString();
        }

        private static string F(double v) =>
            v.ToString("G6", System.Globalization.CultureInfo.InvariantCulture);
        #endregion

        #region ExtractBaseSymbol
        private static string ExtractBaseSymbol(string rawName)
        {
            string name = rawName.Split(' ')[0].ToUpper();
            const string months = "FGHJKMNQUVXZ";
            int i = name.Length - 1;
            if (i > 0 && char.IsDigit(name[i]))
            {
                while (i > 0 && char.IsDigit(name[i])) i--;
                if (i > 0 && months.IndexOf(name[i]) >= 0)
                    name = name.Substring(0, i);
            }
            if (name.Length > 2 && name[0] == 'M')
            {
                string rest = name.Substring(1);
                if (rest == "SIL" || rest == "IL") return "SI";
                return rest;
            }
            if (name == "SIL") return "SI";
            return name;
        }
        #endregion

        private void CloseWriter()
        {
            try { _writer?.Flush(); _writer?.Close(); _writer = null; } catch { }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name="VPS Host", Order=1, GroupName="Fortress Settings",
                 Description="VPS IP address. Leave empty for file-only mode.")]
        public string VpsHost { get; set; }

        [NinjaScriptProperty]
        [Display(Name="VPS Port", Order=2, GroupName="Fortress Settings",
                 Description="TCP port on VPS (must match tick_nt8_bridge_server.py).")]
        public int VpsPort { get; set; }

        [NinjaScriptProperty]
        [Display(Name="Write Files", Order=3, GroupName="Fortress Settings",
                 Description="Also write local JSONL backup files.")]
        public bool WriteFiles { get; set; }

        [NinjaScriptProperty]
        [Display(Name="Output Dir", Order=4, GroupName="Fortress Settings",
                 Description="Local folder for JSONL backup files.")]
        public string OutputDir { get; set; }
        #endregion
    }
}
