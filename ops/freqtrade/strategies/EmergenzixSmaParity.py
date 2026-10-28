"""Freqtrade adapter for Emergenzix's *signal-only* SMA(5,20) baseline.

Both compute rolling means of completed 4h closes. A close-confirmed crossover
flags the signal candle, which Freqtrade backtesting attempts to execute at the
next candle open. This adapter does NOT claim fill/account PnL equivalence:
Freqtrade's wallet, order constraints, fees, slippage and forced exits are
different from emergenzix_api.backtesting.run_sma_crossover.
"""

from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class EmergenzixSmaParity(IStrategy):
    INTERFACE_VERSION = 3
    can_short = False
    timeframe = "4h"
    startup_candle_count = 20
    process_only_new_candles = True

    # Disable ordinary profit-target exits. These are not present in the
    # Emergenzix SMA signal model. The emergency Freqtrade stop remains a
    # documented residual mismatch and is NOT a guarantee against losses.
    minimal_roi = {"0": 100.0}
    stoploss = -0.999
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = False

    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "gtc", "exit": "gtc"}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # pandas' standard rolling mean uses exactly the window and includes
        # the current completed bar; previous values are used for crossover.
        dataframe["sma_fast"] = dataframe["close"].rolling(
            window=5, min_periods=5
        ).mean()
        dataframe["sma_slow"] = dataframe["close"].rolling(
            window=20, min_periods=20
        ).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        up = (
            (dataframe["sma_fast"].shift(1) <= dataframe["sma_slow"].shift(1))
            & (dataframe["sma_fast"] > dataframe["sma_slow"])
        )
        dataframe.loc[up, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        down = (
            (dataframe["sma_fast"].shift(1) >= dataframe["sma_slow"].shift(1))
            & (dataframe["sma_fast"] < dataframe["sma_slow"])
        )
        dataframe.loc[down, "exit_long"] = 1
        return dataframe
