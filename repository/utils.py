import csv
import io
import json
import logging
import zipfile
from collections import namedtuple
from typing import Dict, Optional, Tuple, Union

import requests
from astropy.time import Time
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count
from requests import Response
from rest_framework.renderers import JSONRenderer

from repository.models import Observation, Satellite
from repository.serializers import ObservationSerializer

logger = logging.getLogger(__name__)
# Named tuple to represent additional data from SatChecker for each observation
SatCheckerData = namedtuple(
    "SatCheckerData",
    [
        "phase_angle",
        "range_to_sat",
        "range_rate",
        "illuminated",
        "alt_deg",
        "az_deg",
        "ddec_deg_s",
        "dra_cosdec_deg_s",
        "sat_dec_deg",
        "sat_ra_deg",
        "satellite_name",
        "intl_designator",
    ],
)


# Statistics for main page
def get_stats():
    """
    Retrieves statistics for the main page.

    This function retrieves the count of satellites, observations, and observers,
    as well as a list of the latest observations. If there are no observations,
    it returns a stats object with all fields set to 0 or empty.The latest observations
    are returned as a list of tuples, where each tuple contains an Observation object
    and its JSON representation.

    Returns:
        stats: A namedtuple containing the following fields:
            - satellite_count (int): The total number of satellites.
            - observation_count (int): The total number of observations.
            - observer_count (int): The total number of distinct observers.
            - latest_obs_list (list): A list of the 7 most recent observations, each
              represented as a tuple containing an Observation object and its
              JSON representation.
    """
    stats = namedtuple(
        "stats",
        [
            "satellite_count",
            "observation_count",
            "observer_count",
            "latest_obs_list",
            "observer_locations",
        ],
    )

    observation_count = Observation.objects.count()
    if observation_count == 0:
        return stats(0, 0, 0, [])
    satellite_count = Satellite.objects.count()

    observer_count = (
        Observation.objects.values("location_id", "obs_email").distinct().count()
    )
    latest_obs_list = Observation.objects.order_by("-date_added")[:7]

    # Get all observer locations (the latitude and longitude) and a count of how many
    # observations were made at each location
    observer_locations = (
        Observation.objects.values(
            "location_id", "location_id__obs_lat_deg", "location_id__obs_long_deg"
        )
        .annotate(count=Count("location_id"))
        .order_by("-count")
    )
    observer_locations_list = list(observer_locations)
    observer_locations_json = json.dumps(observer_locations_list)

    # JSON is also needed for the modal view when an observation in the list is clicked
    observation_list_json = [
        (JSONRenderer().render(ObservationSerializer(observation).data))
        for observation in latest_obs_list
    ]
    observations_and_json = zip(latest_obs_list, observation_list_json)

    return stats(
        satellite_count,
        observation_count,
        observer_count,
        observations_and_json,
        observer_locations_json,
    )


# Validate satellite position is above horizon using SatChecker and add additional data
# from the SatChecker response if successful
def add_additional_data(
    satellite_name: str,
    sat_number: int,
    observation_time: Union[str, Time],
    latitude: float,
    longitude: float,
    altitude: float,
) -> Union[SatCheckerData, str, bool]:
    """
    Validates if a satellite is above the horizon at a given time and location and
    returns additional data.

    This function uses the SatChecker API to verify if a satellite, identified by its
    name and number, is above the horizon at a specific time and location. The location
    is specified by latitude, longitude, and altitude. The function returns a
    SatCheckerData namedtuple containing additional data if the satellite is above the
    horizon, and a string error message or False otherwise.

    Args:
        satellite_name (str): The name of the satellite.
        sat_number (int): The catalog number of the satellite.
        observation_time (Union[str, Time]): The time of observation. Can be a string or
                                             an astropy Time object.
        latitude (float): The latitude of the observation location.
        longitude (float): The longitude of the observation location.
        altitude (float): The altitude of the observation location.

    Returns:
        Union[SatCheckerData, str, bool]: Returns a SatCheckerData namedtuple containing
        additional data (phase angle, distance to satellite, height above ground, etc.)
        if the satellite is above the horizon. Returns an error message string or False
        otherwise.
    """
    missing_fields = []

    if not sat_number:
        missing_fields.append("sat_number")
    if not observation_time:
        missing_fields.append("observation_time")
    if not latitude or not (-90 <= latitude <= 90):
        missing_fields.append("latitude")
    if not longitude or not (-180 <= longitude <= 180):
        missing_fields.append("longitude")
    if altitude is None:
        missing_fields.append("altitude")

    if missing_fields:
        missing_fields_str = ", ".join(missing_fields)
        return (
            "Satellite position check failed - check your data. "
            f"Missing or incorrect fields: {missing_fields_str}"
        )
    obs_time = Time(observation_time, format="isot", scale="utc")
    url = "https://satchecker.cps.iau.org/ephemeris/catalog-number/"
    params = {
        "catalog": sat_number,
        "latitude": latitude,
        "longitude": longitude,
        "elevation": altitude,
        "julian_date": obs_time.jd,
        "min_altitude": -90,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
    except requests.exceptions.RequestException:
        return "Satellite position check failed - try again later."

    is_valid = validate_position(r, satellite_name, observation_time)

    if (
        obs_time < Time("2024-05-01T00:00:00.000", format="isot")
        and not is_valid == "archival data"
    ):
        # Temporary fix for satellite name changes

        url = "https://satchecker.cps.iau.org/tools/names-from-norad-id/"
        params = {
            "id": sat_number,
        }

        error = None
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                error = "Satellite info check failed - check the input and try again."
                raise Exception(requests.exceptions.RequestException(error))
            response_json = response.json()
            if not response_json or response_json[0] == []:
                error = "No satellite found for the provided NORAD ID."
                raise Exception(requests.exceptions.RequestException(error))
            else:
                satellite_found = any(
                    item.get("name") == satellite_name for item in response_json
                )
        except requests.exceptions.RequestException:
            error = "Satellite info check failed - try again later."
            is_valid = False
        if error:
            return "Satellite name not found"
        elif satellite_found:
            is_valid = True

    if isinstance(is_valid, str):
        if is_valid == "archival data":
            return SatCheckerData(
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                satellite_name,
                None,
            )
        return is_valid

    if is_valid and r.json():
        satellite_data = r.json()["data"][0]
        fields = r.json().get("fields", [])

        # Mapping fields to their values for easier access
        data_dict = dict(zip(fields, satellite_data))
        satellite_data = SatCheckerData(
            phase_angle=round(data_dict.get("phase_angle_deg", 0), 7),
            range_to_sat=round(data_dict.get("range_km", 0), 7),
            range_rate=round(data_dict.get("range_rate_km_per_sec", 0), 7),
            illuminated=data_dict.get("illuminated"),
            alt_deg=round(data_dict.get("altitude_deg", 0), 7),
            az_deg=round(data_dict.get("azimuth_deg", 0), 7),
            ddec_deg_s=round(data_dict.get("ddec_deg_per_sec", 0), 7),
            dra_cosdec_deg_s=round(data_dict.get("dra_cosdec_deg_per_sec", 0), 7),
            sat_dec_deg=round(data_dict.get("declination_deg", 0), 7),
            sat_ra_deg=round(data_dict.get("right_ascension_deg", 0), 7),
            satellite_name=data_dict.get("name"),
            intl_designator=data_dict.get("international_designator"),
        )
        return satellite_data

    return is_valid


def validate_position(
    response: Response, satellite_name: str, obs_time: Union[str, Time]
) -> Union[str, bool]:
    """
    Validates the position of a satellite based on the response from an API call.

    Args:
        response (Response): The response object from the API call.
        satellite_name (str): The name of the satellite to validate.

    Returns:
        Union[str, bool]: An error message if the validation fails.
                          True if the validation is successful.
    """
    if response.status_code != 200:
        return "Satellite position check failed - verify uploaded data is correct."
    response_data = response.json()
    if not response_data.get("data"):
        return "Satellite with this ID not visible at this time and location"

    satellite_info = response_data["data"][0]
    obs_time = Time(obs_time, format="isot")
    date_str = satellite_info[6].replace(" UTC", "")
    tle_date = Time(date_str, format="iso", scale="utc")
    if (tle_date - obs_time).jd > 14:
        return "archival data"
    if satellite_name and satellite_info[0] != satellite_name:
        return "Satellite name and number do not match"
    if float(satellite_info[9]) < -5:
        return "Satellite below horizon at this time and location"

    return True


def send_confirmation_email(obs_ids: list[int], email_address: Union[str, bool]):
    """
    Sends a confirmation email with observation IDs for reference.

    This function checks if the email backend is in the settings file and returns if
    not. It then creates a text body with the observation list and sends an email with
    the observation IDs.

    Args:
        obs_ids (list[int]): A list of observation IDs.
        email_address (str): The email address to send the confirmation to.

    Returns:
        None
    """
    # check if email backend is in settings file and return if not
    #
    # if not hasattr(settings, "ANYMAIL"):
    #    return
    text_body = get_observation_list(False, obs_ids)

    msg = EmailMultiAlternatives(
        "SCORE Observation Upload Confirmation",
        "SCORE Observation Upload Confirmation \n\n Thank you for submitting your \
            observations. Your observations have been successfully uploaded to the \
                SCORE database.  The observation ID(s) are: \n\n"
        + text_body,
        settings.EMAIL_HOST_USER,
        [email_address],
    )

    email_body = """
    <html>
        <h2>SCORE Observation Upload Confirmation</h2>
        <p>
            Thank you for submitting your observations. Your observations have
            been successfully uploaded to the SCORE database.
            The observation ID(s) are:
        </p>
    """
    email_body += get_observation_list(True, obs_ids)
    email_body += "</html>"
    msg.attach_alternative(email_body, "text/html")
    msg.send()


# Create list of observations with supplemental details for upload confirmation
def get_observation_list(is_html: bool, obs_ids: list[int]) -> str:
    """
    Creates a list of observations with supplemental details for upload confirmation.

    This function iterates over a list of observation IDs, retrieves each observation
    from the database, and appends a string with the observation's ID, satellite name,
    and observation time to the list text. The format of the list text depends on
    whether it is intended to be used in HTML or not.

    Args:
        is_html (bool): A flag indicating whether the list text is intended to be used
                        in HTML.
        obs_ids (list[int]): A list of observation IDs.

    Returns:
        str: The list text.
    """
    list_text = ""

    for obs_id in obs_ids:
        observation = Observation.objects.get(id=obs_id)
        list_text += (
            str(obs_id)
            + (
                " - " + observation.satellite_id.sat_name
                if observation.satellite_id.sat_name
                else ""
            )
            + " - "
            + str(observation.satellite_id.sat_number)
            + " - "
            + str(observation.obs_time_utc)
            + "<br />"
            if is_html
            else "\n"
        )
    return list_text


def send_data_change_email(contact_email: str, obs_ids: str, reason: str):
    """
    Sends a data change email with observation IDs, reason for change, and contact email

    Args:
        contact_email (str): The contact email from the form.
        obs_ids (str): The observation IDs from the form.
        reason (str): The reason for data change/deletion from the form.
        email_address (str): The email address to send the data change email to.

    Returns:
        None
    """
    text_body = f"""
    Contact Email: {contact_email}
    Observation IDs: {obs_ids}
    Reason for Data Change/Deletion: {reason}
    """

    msg = EmailMultiAlternatives(
        "SCORE Data Change Request",
        text_body,
        settings.EMAIL_HOST_USER,
        [settings.EMAIL_HOST_USER],
    )

    email_body = f"""
    <html>
    <h2>SCORE Data Change Request</h2>
    <p>Contact Email: {contact_email}</p>
    <p>Observation IDs: {obs_ids}</p>
    <p>Reason for Data Change/Deletion: {reason}</p>
    </html>
    """
    msg.attach_alternative(email_body, "text/html")
    msg.send()


# CSV header - same as upload format minus the email address for privacy
def get_csv_header() -> list[str]:
    """
    Returns the header for the CSV file.

    This function returns a list of strings representing the header of the CSV file.
    The header includes the names of all the fields in the CSV file, excluding the
    email address for privacy.

    Returns:
        list[str]: A list of strings representing the header of the CSV file.
    """
    header = [
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
        "sat_ra_deg_satchecker",
        "sat_dec_deg_satchecker",
        "range_to_sat_km_satchecker",
        "range_rate_sat_km_s_satchecker",
        "ddec_deg_s_satchecker",
        "dra_cosdec_deg_s_satchecker",
        "phase_angle_deg_satchecker",
        "alt_deg_satchecker",
        "az_deg_satchecker",
        "illuminated",
        "international_designator",
    ]
    return header


def create_csv(
    observation_list: list[Observation], satellite_name: str
) -> Tuple[io.BytesIO, str]:
    """
    Creates a CSV file from a list of observations and compresses it into a zip file.

    This function takes a list of Observation objects, generates a CSV file with the
    details of each observation, and compresses the CSV file into a zip file. If the
    observation list is empty, it retrieves all observations from the database.
    The CSV file includes a header row with the names of all the fields.

    Args:
        observation_list (list[Observation]): A list of Observation objects.

    Returns:
        Tuple[io.BytesIO, str]: A tuple containing the compressed zip file and the
        name of the zip file.
    """
    all_observations = False
    if not observation_list:
        observation_list = Observation.objects.all()
        all_observations = True

    header = get_csv_header()

    csv_lines = []
    for observation in observation_list:
        # format ORC ID string properly - remove brackets and only have quotes around
        # the field if there is more than one ORCID separated by commas (otherwise no
        # quotes)

        if isinstance(observation.obs_orc_id, list):
            orc_id = ", ".join(observation.obs_orc_id)
        else:
            orc_id = observation.obs_orc_id.replace("[", "").replace("]", "")

        if "," in orc_id:
            orc_id = f'"{orc_id}"'

        # format date/time to match upload format
        obs_time_utc = observation.obs_time_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        obs_time_utc = obs_time_utc[:-4] + "Z"  # Trim to 3 digits

        csv_lines.append(
            [
                observation.satellite_id.sat_name,
                observation.satellite_id.sat_number,
                obs_time_utc,
                observation.obs_time_uncert_sec,
                observation.apparent_mag,
                observation.apparent_mag_uncert,
                observation.location_id.obs_lat_deg,
                observation.location_id.obs_long_deg,
                observation.location_id.obs_alt_m,
                observation.limiting_magnitude,
                observation.instrument,
                observation.obs_mode,
                observation.obs_filter,
                orc_id,
                observation.sat_ra_deg,
                observation.sat_dec_deg,
                observation.sigma_2_ra,
                observation.sigma_ra_sigma_dec,
                observation.sigma_2_dec,
                observation.range_to_sat_km,
                observation.range_to_sat_uncert_km,
                observation.range_rate_sat_km_s,
                observation.range_rate_sat_uncert_km_s,
                observation.comments,
                observation.data_archive_link,
                observation.mpc_code,
                observation.sat_ra_deg_satchecker,
                observation.sat_dec_deg_satchecker,
                observation.range_to_sat_km_satchecker,
                observation.range_rate_sat_km_s_satchecker,
                observation.ddec_deg_s_satchecker,
                observation.dra_cosdec_deg_s_satchecker,
                observation.phase_angle,
                observation.alt_deg_satchecker,
                observation.az_deg_satchecker,
                observation.illuminated,
                observation.satellite_id.intl_designator,
            ]
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(csv_lines)

    if satellite_name:
        zipfile_name = f"{satellite_name}_observations.zip"
    else:
        zipfile_name = (
            "satellite_observations_all.zip"
            if all_observations
            else "satellite_observations_search_results.zip"
        )

    zipped_file = io.BytesIO()

    with zipfile.ZipFile(zipped_file, "w") as zip:
        zip.writestr("observations.csv", output.getvalue())
    zipped_file.seek(0)

    return zipped_file, zipfile_name


def get_satellite_name(norad_id):
    """
    Queries the SatChecker API to get the name of the satellite associated with the
    provided NORAD ID.

    The function sends a GET request to the SatChecker API with the NORAD ID as a
    parameter. If the API returns a response, the function extracts the satellite name
    from the response and returns it. If the API does not return a response, or if the
    response does not contain a satellite name, the function returns None.

    If an error occurs while sending the request or processing the response, the
    function catches the exception and returns None.

    Parameters:
    norad_id (str): The NORAD ID of the satellite.

    Returns:
    str or None: The name of the satellite associated with the NORAD ID, or None if no
    satellite was found or an error occurred.
    """
    url = "https://satchecker.cps.iau.org/tools/names-from-norad-id/"
    params = {"id": norad_id}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        if not response.json() or response.json()[0] == []:
            return None
        data = response.json()
        for item in data:
            if item["is_current_version"] is True:
                return item["name"]
    except requests.exceptions.RequestException:
        return None


def get_norad_id(satellite_name):
    """
    Queries the SatChecker API to get the NORAD ID of the satellite associated with the
    provided name.

    The function sends a GET request to the SatChecker API with the satellite name as a
    parameter. If the API returns a response, the function extracts the NORAD ID from
    the response and returns it. If the API does not return a response, or if the
    response does not contain a NORAD ID, the function returns None.

    If an error occurs while sending the request or processing the response, the
    function catches the exception and returns None.

    Parameters:
    satellite_name (str): The name of the satellite.

    Returns:
    str or None: The NORAD ID of the satellite associated with the name, or None if no
    satellite was found or an error occurred.
    """
    url = "https://satchecker.cps.iau.org/tools/norad-ids-from-name/"
    params = {"name": satellite_name}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data:
            return None

        for item in data:
            if item["is_current_version"] is True:
                return item["norad_id"]

    except requests.exceptions.RequestException:
        return None


# Query SatChecker API for satellite metadata
def get_satellite_metadata(satellite_number: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Query SatChecker API for satellite metadata.

    Parameters:
        satellite_number (str): The satellite catalog number.

    Returns:
        Optional[Dict[str, Optional[str]]]: A dictionary containing satellite metadata,
        or None if the request fails or no data is found.
    """
    satchecker_url = f"https://satchecker.cps.iau.org/tools/get-satellite-data/?id={satellite_number}&id_type=catalog"

    try:
        response = requests.get(satchecker_url, timeout=10)
        response.raise_for_status()
        satellite_data = response.json()

        if satellite_data:
            metadata = satellite_data[0]
            return {
                "rcs_size": metadata.get("rcs_size"),
                "object_type": metadata.get("object_type"),
                "launch_date": metadata.get("launch_date"),
                "decay_date": metadata.get("decay_date"),
                "name": metadata.get("name"),
                "norad_id": metadata.get("norad_id"),
                "international_designator": metadata.get("international_designator"),
            }
        else:
            logger.warning(f"No metadata found for satellite {satellite_number}")
    except requests.RequestException as e:
        logger.error(
            f"Error fetching metadata for satellite {satellite_number}: {str(e)}"
        )

    return None
