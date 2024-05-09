import numpy as np
import pandas as pd

# Assuming `df` is your DataFrame
df = pd.DataFrame(
    {
        "name": ["satellite1", "satellite2"],
        "id": ["12345", "23456"],
        "time": ["2021-01-01T12:00:00Z", "2021-01-02T12:00:00Z"],
        "uncertainty": [1, 2],
        "magnitude": [1, 2],
        "magnitude_uncertainty": [0.1, 0.2],
        "latitude": [33, 33],
        "longitude": [-110, -110],
        "observer_altitude_m": [1000, 1000],
        "limiting_magnitude": [6, 6],
        "instrument": ["instrument1", "instrument2"],
        "observing_mode": ["CCD", "CCD"],
        "observing_filter": ["R", "V"],
        "observer_email": ["sample@user.email", "sample@user.email"],
        "observer_orcid": ["0000-0000-0000-0000", "0000-0000-0000-0000"],
    }
)

# Rename the DataFrame columns to match the SCORE column names
df_score = df.rename(
    columns={
        "name": "satellite_name",
        "id": "norad_cat_id",
        "time": "observation_time_utc",
        "uncertainty": "observation_time_uncertainty_sec",
        "magnitude": "apparent_magnitude",
        "magnitude_uncertainty": "apparent_magnitude_uncertainty",
        "latitude": "observer_latitude_deg",
        "longitude": "observer_longitude_deg",
    }
)

# All SCORE related columns in order
columns_order = [
    "satellite_name",
    "norad_cat_id",
    "observation_time_utc",
    "observation_time_uncertainty_sec",
    "apparent_magnitude",
    "apparent_magnitude_uncertainty",
    "observer_latitude_deg",
    "observer_longitude_deg",
    "observer_altitude_m",
    "limiting_magnitude",
    "instrument",
    "observing_mode",
    "observing_filter",
    "observer_email",
    "observer_orcid",
    "satellite_right_ascension_deg",
    "satellite_declination_deg",
    "sigma_2_ra",
    "sigma_ra_sigma_dec",
    "sigma_2_dec",
    "range_to_satellite_km",
    "range_to_satellite_uncertainty_km",
    "range_rate_of_satellite_km_per_sec",
    "range_rate_of_satellite_uncertainty_km_per_sec",
    "comments",
    "data_archive_link",
    "mpc_code",
]

# Reindex the DataFrame with the desired columns
df_score = df_score.reindex(columns=columns_order)

# Replace NaN values with an empty string
df_score = df_score.replace(np.nan, "", regex=True)

# Use the `to_csv` method to create a CSV file
df_score.to_csv("score_upload.csv", index=False, columns=columns_order)
