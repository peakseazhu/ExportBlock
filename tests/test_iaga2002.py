from __future__ import annotations

from pathlib import Path

import pandas as pd

from exportblock.io.iaga2002 import read_iaga2002_file


def test_read_iaga2002_minimal(tmp_path: Path):
    p = tmp_path / "kak_test.min"
    p.write_text(
        "\n".join(
            [
                " Format                 IAGA-2002                                    |",
                " IAGA Code              KAK                                          |",
                " Geodetic Latitude      36.232                                       |",
                " Geodetic Longitude     140.186                                      |",
                " Elevation              36                                           |",
                " Reported               XYZG                                         |",
                "DATE       TIME         DOY     KAKX      KAKY      KAKZ      KAKG   |",
                "2020-01-01 00:00:00.000 001     1.00      2.00      3.00      4.00",
                "2020-01-01 00:01:00.000 001     99999.00  2.00      3.00      4.00",
            ]
        ),
        encoding="utf-8",
    )

    df, meta = read_iaga2002_file(p, source="geomag")
    assert meta["iaga_code"] == "KAK"
    assert df["station_id"].nunique() == 1
    assert set(df["channel"].unique()) == {"X", "Y", "Z", "G"}

    x = df[df["channel"] == "X"].sort_values("ts_ms").reset_index(drop=True)
    assert x.loc[0, "value"] == 1.0
    assert pd.isna(x.loc[1, "value"])


def test_read_aef_keep_z(tmp_path: Path):
    p = tmp_path / "kak_daef.min"
    p.write_text(
        "\n".join(
            [
                " Format                 IAGA-2002                                    |",
                " IAGA CODE              KAK                                          |",
                " Geodetic Latitude      36.232                                       |",
                " Geodetic Longitude     140.186                                      |",
                " Elevation              36                                           |",
                " Reported               XYZF                                         |",
                "DATE       TIME         DOY     KAKX      KAKY      KAKZ      KAKF   |",
                "2020-01-01 00:00:00.000 001     88888.00  88888.00  62.70     88888.00",
                "2020-01-01 00:01:00.000 001     88888.00  88888.00  99999.90  88888.00",
            ]
        ),
        encoding="utf-8",
    )
    df, _ = read_iaga2002_file(p, source="aef", keep_channels={"Z"})
    assert set(df["channel"].unique()) == {"Z"}
    assert df.shape[0] == 2
    assert df["value"].isna().sum() == 1

