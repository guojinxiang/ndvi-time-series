## Install Instructions
- Download the Google Cloud SDK for Python
   * https://cloud.google.com/sdk/
- Create an App Engine Project
   * https://console.cloud.google.com/
- Create a service account and request an authentication for the Earth Engine
   * https://developers.google.com/earth-engine/service_account
- Add your App Engine Project to Firebase
   * https://console.firebase.google.com/
- Download the Firebase Web Config Html file into the templates folder
   * And allow public reads in your firebase database rules
- Update the credentials and your application id
   * Copy the private key json file into the root folder of the downloaded source code.
   * Update SERVICE_ACC_JSON_KEYFILE in `/config.py`.
   * Update FIREBASE_CONFIG in `/config.py`.
- Load the required python libraries
   * Use `pip install -t lib -r requirements.txt` to load all required libraries into the `lib` folder
- Import the project into your Google Cloud SDK installation and start the debug server