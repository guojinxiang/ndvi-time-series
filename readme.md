## Google App Engine NDVI Time Series Tool
This tool aggregates multiple Landsat satellite images for the given years and location and calculates the coefficients of the chosen regression for the Normalized Differenced Vegetation Index (NDVI) values at each pixel.

Live version at: https://ndvi-time-series.appspot.com/
## Install Instructions
- Download the Google App Engine SDK for Python
   * https://cloud.google.com/appengine/downloads
- Create an App Engine Project
   * https://console.cloud.google.com/
- Create a service account and request an authentication for the Earth Engine
   * https://developers.google.com/earth-engine/service_account
- Load the required python libraries
   * Use `pip install -t lib -r requirements.txt` to load all required libraries into the `lib` folder
- Update the credentials and your application id
   * Copy the private key json file into the root folder of the downloaded source code.
   * Update SERVICE_ACC_JSON_KEYFILE in `/config.py`.
   * Insert your application id into `/app.yaml`
- Import the project into your App Engine SDK installation and start the debug server