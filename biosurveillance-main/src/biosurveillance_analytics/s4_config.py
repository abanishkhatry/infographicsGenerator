"""Declares REDCap access credentials."""
import os

api_url = "https://redcap.wisc.edu/api/"
# api_url = "https://test.redcap.wisc.edu/api/"

api_token = os.environ.get('S4_REDCAP_API_TOKEN_PROD')

# api_token = os.environ.get('S4_REDCAP_API_TOKEN_UAT')

#create data directories if they don't exist
# if not os.path.exists(protected_data_store + "data\\"):
#     os.makedirs(protected_data_store + "data\\")

# if not os.path.exists(protected_data_store + "timelines\\"):
#     os.makedirs(protected_data_store + "timelines\\")