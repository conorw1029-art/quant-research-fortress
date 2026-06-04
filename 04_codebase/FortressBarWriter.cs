// FortressBarWriter.cs
// =====================================================================
// NinjaTrader 8 Indicator — writes OHLCV + CVD + DOM bar data to JSONL
// files for consumption by the Python Fortress executor.
//
// HOW TO INSTALL:
//   1. In NinjaTrader 8: Tools -> NinjaScript Editor -> New -> Indicator
//   2. Name it exactly: FortressBarWriter
//   3. Delete all default code and paste this entire file
//   4. Click Compile (F5). Fix any errors shown in the Output window.
//   5. Close the editor.
//
// HOW TO USE:
//   1. Open a chart for each instrument: MGC 09-26, MES 09-26, MNQ 09-26, SIL 09-26
//   2. Right-click chart -> Indicators -> Add -> FortressBarWriter
//   3. Set bar period to match your strategy (1 min, 3 min, 5 min, etc.)
//      Apply ONE copy per timeframe per instrument.
//   4. Set Output Directory to your quant-research path (see default below)
//   5. Keep the charts open. The indicator writes on every bar close.
//
// OUTPUT FILES (one per symbol per timeframe):
//   {OutputDir}\GC_1m_live.jsonl
//   {OutputDir}\GC_3m_live.jsonl
//   {OutputDir}\ES_1m_live.jsonl
//   {OutputDir}\SI_1m_live.jsonl  etc.
//
// Each line is one completed bar as JSON:
//   {"ts":"2026-06-04T14:32:00Z","open":2310.5,"high":2312.0,...}
//
// Python reads these via tick_live_bar_reader.py
// =====================================================================

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Text;
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
        private double       _tickSize;

        #region OnStateChange
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Fortress Bar Writer — streams OHLCV+CVD+DOM bars to Python executor";
                Name        = "FortressBarWriter";
                Calculate   = Calculate.OnEachTick;
                IsOverlay   = true;
                DisplayInDataBox      = false;
                IsSuspendedWhileInactive = false;

                // Default path — update to match your actual Desktop path
                OutputDir = System.IO.Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    "Desktop", "quant-research", "01_data", "tick_bars", "live"
                );
            }
            else if (State == State.DataLoaded)
            {
                _tickSize   = Instrument.MasterInstrument.TickSize;
                _baseSymbol = ExtractBaseSymbol(Instrument.MasterInstrument.Name);
                _isFirstBar = true;
                _prevClose  = 0;
                _buyVol     = 0;
                _sellVol    = 0;
                _cumCvd     = 0;

                // Initialise DOM arrays to zero
                for (int i = 0; i < 10; i++)
                {
                    _bidPx[i] = 0; _bidSz[i] = 0;
                    _askPx[i] = 0; _askSz[i] = 0;
                }

                string barMin = BarsPeriod.Value.ToString();
                try
                {
                    Directory.CreateDirectory(OutputDir);
                    _filePath = Path.Combine(OutputDir, $"{_baseSymbol}_{barMin}m_live.jsonl");
                    _writer   = new StreamWriter(_filePath, append: true, encoding: Encoding.UTF8);
                    _writer.AutoFlush = true;
                    Print($"[Fortress] {_baseSymbol} {barMin}m -> {_filePath}");
                }
                catch (Exception ex)
                {
                    Print($"[Fortress] ERROR opening file: {ex.Message}");
                }
            }
            else if (State == State.Terminated)
            {
                CloseWriter();
            }
        }
        #endregion

        #region OnBarUpdate
        protected override void OnBarUpdate()
        {
            // IsFirstTickOfBar = we are on the first tick of a NEW bar.
            // That means the PREVIOUS bar just closed — write it.
            if (IsFirstTickOfBar && !_isFirstBar && CurrentBar > 0)
            {
                WriteCompletedBar();
                _buyVol  = 0;
                _sellVol = 0;
            }
            _isFirstBar = false;

            // Accumulate volume delta on every tick (except boundary)
            if (IsFirstTickOfBar) return;

            double px  = Close[0];
            double vol = Volume[0];

            // Aggressor-side classification:
            // Price at or above ask → buyer lifted the offer → buy
            // Price at or below bid → seller hit the bid → sell
            // Otherwise tick-rule fallback
            double ask0 = _askPx[0];
            double bid0 = _bidPx[0];

            if (ask0 > 0 && px >= ask0 - _tickSize * 0.5)
                _buyVol += vol;
            else if (bid0 > 0 && px <= bid0 + _tickSize * 0.5)
                _sellVol += vol;
            else if (_prevClose > 0)
            {
                // Tick rule
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
            {
                _askPx[pos] = e.Price;
                _askSz[pos] = e.Volume;
            }
            else if (e.MarketDataType == MarketDataType.Bid)
            {
                _bidPx[pos] = e.Price;
                _bidSz[pos] = e.Volume;
            }
        }
        #endregion

        #region WriteCompletedBar
        private void WriteCompletedBar()
        {
            if (_writer == null) return;
            try
            {
                _cumCvd += (_buyVol - _sellVol);

                double bid0   = _bidPx[0];
                double ask0   = _askPx[0];
                double spread = (ask0 > 0 && bid0 > 0) ? ask0 - bid0 : 0;

                // L1 book pressure
                double bSz1  = _bidSz[0];
                double aSz1  = _askSz[0];
                double tot1  = bSz1 + aSz1;
                double bp    = (tot1 > 0) ? (bSz1 - aSz1) / tot1 : 0;

                // OBI top-5
                double bTot5 = 0, aTot5 = 0;
                for (int i = 0; i < 5; i++) { bTot5 += _bidSz[i]; aTot5 += _askSz[i]; }
                double tot5  = bTot5 + aTot5;
                double obi5  = (tot5 > 0) ? (bTot5 - aTot5) / tot5 : 0;

                // Microprice
                double micro = (tot1 > 0) ? (bid0 * aSz1 + ask0 * bSz1) / tot1 : (bid0 + ask0) / 2.0;

                // Build DOM arrays (5 levels each side)
                var sb = new StringBuilder();
                sb.Append("{");
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

                // Bid/ask levels 0-4
                for (int i = 0; i < 5; i++)
                {
                    sb.AppendFormat("\"bid_px_{0:D2}\":{1},", i, F(_bidPx[i]));
                    sb.AppendFormat("\"ask_px_{0:D2}\":{1},", i, F(_askPx[i]));
                    sb.AppendFormat("\"bid_sz_{0:D2}\":{1},", i, (long)_bidSz[i]);
                    sb.AppendFormat("\"ask_sz_{0:D2}\":{1},", i, (long)_askSz[i]);
                }

                sb.AppendFormat("\"n_trades\":{0}", (long)(_buyVol + _sellVol));
                sb.Append("}");

                _writer.WriteLine(sb.ToString());
            }
            catch (Exception ex)
            {
                Print($"[Fortress] Write error: {ex.Message}");
            }
        }

        // Format double — 6 significant decimal places, no trailing zeros issue
        private static string F(double v) => v.ToString("G6", System.Globalization.CultureInfo.InvariantCulture);
        #endregion

        #region ExtractBaseSymbol
        private static string ExtractBaseSymbol(string rawName)
        {
            // "MGC 09-26" -> "GC"
            // "MGCU5"     -> "GC"
            // "MESU5"     -> "ES"
            // "SIL 09-26" -> "SI"
            string name = rawName.Split(' ')[0].ToUpper();

            // Strip trailing month code + year digits (e.g. "U5", "M26")
            const string months = "FGHJKMNQUVXZ";
            int i = name.Length - 1;
            while (i > 0 && char.IsDigit(name[i])) i--;
            if (i > 0 && months.IndexOf(name[i]) >= 0)
                name = name.Substring(0, i);

            // Micro prefix: MGC->GC, MES->ES, MNQ->NQ, MCL->CL
            if (name.Length > 2 && name[0] == 'M')
            {
                string rest = name.Substring(1);
                // SIL special case
                if (rest == "SIL" || rest == "IL") return "SI";
                return rest;
            }
            if (name == "SIL") return "SI";

            return name;
        }
        #endregion

        private void CloseWriter()
        {
            try { _writer?.Flush(); _writer?.Close(); _writer = null; }
            catch { }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Output Directory", Order = 1, GroupName = "Fortress Settings",
                 Description = "Full path to quant-research\\01_data\\tick_bars\\live")]
        public string OutputDir { get; set; }
        #endregion
    }
}

#region NinjaScript generated code
namespace NinjaTrader.NinjaScript.Indicators
{
    public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
    {
        private FortressBarWriter[] cacheFortressBarWriter;
        public FortressBarWriter FortressBarWriter(string outputDir)
        {
            return FortressBarWriter(Input, outputDir);
        }

        public FortressBarWriter FortressBarWriter(ISeries<double> input, string outputDir)
        {
            if (cacheFortressBarWriter != null)
                for (int idx = 0; idx < cacheFortressBarWriter.Length; idx++)
                    if (cacheFortressBarWriter[idx] != null && cacheFortressBarWriter[idx].OutputDir == outputDir
                        && cacheFortressBarWriter[idx].EqualsInput(input))
                        return cacheFortressBarWriter[idx];
            return CacheIndicator<FortressBarWriter>(new FortressBarWriter(){ OutputDir = outputDir }, input, ref cacheFortressBarWriter);
        }
    }
}
#endregion
