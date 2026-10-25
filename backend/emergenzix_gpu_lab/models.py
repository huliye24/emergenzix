"""Small causal PyTorch regressors; no trading or exchange integration."""

from __future__ import annotations

import torch
from torch import nn


class MLPRegressor(nn.Module):
    def __init__(self, lookback: int, features: int, width: int = 64) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Flatten(), nn.Linear(lookback * features, width), nn.ReLU(),
            nn.Linear(width, width // 2), nn.ReLU(), nn.Linear(width // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x).squeeze(-1)


class CausalConv1d(nn.Module):
    def __init__(self, channels_in: int, channels_out: int, dilation: int) -> None:
        super().__init__()
        self.padding = 2 * dilation
        self.conv = nn.Conv1d(channels_in, channels_out, kernel_size=3,
                              dilation=dilation, padding=self.padding)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output = self.conv(x)
        return output[..., :-self.padding]


class TCNRegressor(nn.Module):
    def __init__(self, features: int, width: int = 32) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            CausalConv1d(features, width, 1), nn.ReLU(),
            CausalConv1d(width, width, 2), nn.ReLU(),
            CausalConv1d(width, width, 4), nn.ReLU(),
        )
        self.head = nn.Linear(width, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        representation = self.layers(x.transpose(1, 2))
        return self.head(representation[..., -1]).squeeze(-1)


def make_model(name: str, *, lookback: int, features: int) -> nn.Module:
    if name == "mlp":
        return MLPRegressor(lookback, features)
    if name == "tcn":
        return TCNRegressor(features)
    raise ValueError("model must be mlp or tcn")
