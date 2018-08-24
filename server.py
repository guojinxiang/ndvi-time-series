#!/usr/bin/env python
"""Web server for the NDVI Time Series Tool application.

The code in this file runs on App Engine. It's called when the user loads the
web page, requests a map or chart and if he wants to export an image.

The App Engine code does most of the communication with EE. It uses the
EE Python library and the service account specified in config.py. The
exception is that when the browser loads map tiles it talks directly with EE.

The map handler generates a unique client ID for the Channel API connection,
injects it into the index.html template, and returns the page contents.

When the user changes the options in the UI and clicks the compute button, the /mapid handler will generated
map IDs for each image band.

When the user requests a chart the /chart handler generates and returns a small chart over the Channel API.
Also a full screen version is temporary available (ids are saved with the Memcache API) where the chart can
be saved as image or table.

When the user exports a file, the /export handler then kicks off an export
runner (running asynchronously) to create the EE task and poll for the task's
completion. When the EE task completes, the file is stored for 5 hours in the service
account's Drive folder and an download link is sent to the user's browser using the Channel API.

To clear the service account's Drive folder a cron job runs every hour and deletes all files older than 5 hours.

Another export method is the /download handler that generates a download url directly from the EE.
With this method the computing is done on the fly, because of that the download is not very stable and
the file size is limited by 1024 MB.
"""

import math
import traceback
import json
import logging
import os
import random
import socket
import string
import time
import calendar
import urlparse
import re
from datetime import datetime

# ctypes PATH KeyError fix
os.environ.setdefault("PATH", '')

import httplib2
import firebase_admin
from firebase_admin import auth as firebase_auth
import ee
import jinja2

from oauth2client.service_account import ServiceAccountCredentials
import webapp2
import gviz_api

from google.appengine.api import taskqueue
from google.appengine.api import urlfetch
from google.appengine.api import memcache
from google.appengine.api import users

import config
import drive


###############################################################################
#                               Initialization.                               #
###############################################################################

# Debug flag controls the output of the stacktrace if errors occur
DEBUG = True

# The timeout for URL Fetch, Socket and Earth Engine (seconds).
# Note: Normal request are terminated after 60 seconds, background requests after 10 Minutes
URL_FETCH_TIMEOUT = 600  # 10 Minuten

# Check https://developers.google.com/drive/scopes for all available scopes.
# Compines the Drive, Earth Engine and Firebase Scopes
OAUTH_SCOPES = ["https://www.googleapis.com/auth/drive"] + ["https://www.googleapis.com/auth/earthengine","https://www.googleapis.com/auth/devstorage.full_control"] + ["https://www.googleapis.com/auth/userinfo.email","https://www.googleapis.com/auth/firebase.database"]

# Our App Engine service account's credentials for Earth Engine and Google Drive
CREDENTIALS = ServiceAccountCredentials.from_json_keyfile_name(config.SERVICE_ACC_JSON_KEYFILE, OAUTH_SCOPES)


# Initialize the EE API.
ee.Initialize(CREDENTIALS)
# Set some timeouts
ee.data.setDeadline(URL_FETCH_TIMEOUT*1000)  # in milliseconds (default no limit)
socket.setdefaulttimeout(URL_FETCH_TIMEOUT)
urlfetch.set_default_fetch_deadline(URL_FETCH_TIMEOUT)

# The Jinja templating system we use to dynamically generate HTML. See:
# http://jinja.pocoo.org/docs/dev/
JINJA2_ENVIRONMENT = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
        autoescape=True,
        extensions=["jinja2.ext.autoescape"])

# An authenticated Drive helper object for the app service account.
DRIVE_HELPER = drive.DriveHelper(CREDENTIALS)

# The resolution of the exported images (meters per pixel).
EXPORT_RESOLUTION = 30

# The maximum number of pixels in an exported image.
EXPORT_MAX_PIXELS = 10e10

# The frequency to poll for export EE task completion (seconds).
TASK_POLL_FREQUENCY = 10


###############################################################################
#                             Web request handlers.                           #
###############################################################################

class DataHandler(webapp2.RequestHandler):

    """A servlet base class for responding to data queries.

    We use this base class to wrap our web request handlers with try/except
    blocks and set per-thread values (e.g. URL_FETCH_TIMEOUT).
    """

    def get(self):
        self.Handle(self.DoGet)

    def post(self):
        self.Handle(self.DoPost)

    def DoGet(self):
        """Processes a GET request and returns a JSON-encodable result."""
        raise NotImplementedError()

    def DoPost(self):
        """Processes a POST request and returns a JSON-encodable result."""
        raise NotImplementedError()

    def Handle(self, handle_function):
        """Responds with the result of the handle_function or errors, if any."""
        try:
            response = handle_function()
        except Exception as e:
            if DEBUG:
                response = {"error": str(e) + " - " + traceback.format_exc()}
            else:
                response = {"error": str(e)}
        if response:
            self.response.headers["Content-Type"] = "application/json"
            self.response.out.write(json.dumps(response))


class MapHandler(DataHandler):

    """A servlet to handle requests to load the main web page."""

    def DoGet(self):
        """Returns the main web page with Firebase details included."""
        client_id = _GetUniqueString()

        template = JINJA2_ENVIRONMENT.get_template("templates/index.html")
        self.response.out.write(template.render({
                # channel token expire in 24 hours
                "clientId": client_id,
                "firebaseToken": create_custom_token(client_id),
                "firebaseConfig": "templates/%s" % config.FIREBASE_CONFIG,
                "display_splash": "none"
        }))


class MapIdHandler(DataHandler):

    """A servlet that generates the map IDs for each band based on the selected options"""

    def DoPost(self):
        """Returns the map IDs of the requested options.

        HTTP Parameters:
            regression: the regression type [poly1,poly2,poly3,zhuWood]
            source: the source satellite [all,land5,land7,land8]
            start: the start year to filter the satellite images (including)
            end: the end year to filter the satellite images (including)
            cloudscore: the max cloudscore for the ee.Algorithms.Landsat.simpleCloudScore [1-100]
                        Higher means that the pixel is more likley to be a cloud
            point: an array of two double values representing coordinates like [<longitude>,<latitude>]
            region: an array of arrays representing a region [[<longitude>,<latitude>],[<longitude>,<latitude>],...]
            client_id: the unique id that is used for the channel api

        Returns:
            A dictionary with a key called 'bands' containing an array of dictionaries
                like {"name":<band name>,"mapid":<mapid>,"token":<token>}.
        """

        # reads the request options
        options = _ReadOptions(self.request)

        # creates an image based on the options
        image = _GetImage(options)

        # _GetImage returns None if the collection is empty
        if image is None:
            return {"error": "No images in collection. Change your options."}

        bands = image.bandNames().getInfo()
        layers = []
        for band in bands:
            # create a map overlay for each band
            mapid = image.select(band).visualize().getMapId()
            layers.append({"name":band, "mapid": mapid["mapid"], "token": mapid["token"]})
        return {"bands":layers}


class ChartHandler(DataHandler):

    """A servlet to handle chart requests"""

    def DoGet(self):
        """Returns the full screen view of a chart.

        HTTP Parameters:
            id: the unique chart id (key value for the Memcache API).

        Returns:
            A html page with the full screen chart
        """
        chart_id = self.request.get("id")

        # load chart options from Memcache API
        chart_options = memcache.get(chart_id)

        if chart_options is None:
            return {"error":"Chart id doesn't exist!"}
        else:
            # read template file
            f = open("templates/full_chart.html", "r")
            full_chart = f.read()
            f.close()

            # style chart view corresponding to the regression type
            if chart_options["regression"] == "zhuWood":
                chart_options["chart_style"] = "height: 40%;"
                chart_options["chartArea"] = "{width: \"80%\"}"
            else:
                chart_options["chart_style"] = "height: 60%; max-width: 1000px;"
                chart_options["chartArea"] = "{width: \"70%\"}"

            # output html page
            self.response.set_status(200)
            self.response.headers["Content-Type"] = "text/html"
            self.response.out.write(full_chart % chart_options)
            return

    def DoPost(self):
        """Starts an ChartRunnerHandler to asynchronously generate a chart.

        HTTP Parameters:
            regression: the regression type [poly1,poly2,poly3,zhuWood]
            source: the source satellite [all,land5,land7,land8]
            start: the start year to filter the satellite images (including)
            end: the end year to filter the satellite images (including)
            cloudscore: the max cloudscore for the ee.Algorithms.Landsat.simpleCloudScore [1-100]
                        Higher means that the pixel is more likley to be a cloud
            point: an array of two double values representing coordinates like [<longitude>,<latitude>]
            client_id: the unique id that is used for the channel api
        """
        # read request options
        options = _ReadOptions(self.request)

        # Kick off an export runner to start and monitor the EE export task.
        # Note: The work "task" is used by both Earth Engine and App Engine to refer
        # to two different things. "TaskQueue" is an async App Engine service.
        # only execute once even if task fails
        taskqueue.add(url="/chartrunner", params={"options":json.dumps(options)}, retry_options=taskqueue.TaskRetryOptions(task_retry_limit=0,task_age_limit=1))

        # notify client browser that the chart creation has started
        _SendMessage(options["client_id"],"chart-" + options["filename"],"info","Chart creation at [%s/%s] in progress." % (options["point"][1],options["point"][0]))


class ChartRunnerHandler(webapp2.RequestHandler):

    """A servlet for handling async chart task requests."""

    def post(self):
        """Generates a small chart that is displayed as alert in the clients browser
            and creates the full screen version that is saved with the Memcache API.

        HTTP Parameters:
            regression: the regression type [poly1,poly2,poly3,zhuWood]
            source: the source satellite [all,land5,land7,land8]
            start: the start year to filter the satellite images (including)
            end: the end year to filter the satellite images (including)
            cloudscore: the max cloudscore for the ee.Algorithms.Landsat.simpleCloudScore [1-100]
                        Higher means that the pixel is more likley to be a cloud
            point: an array of two double values representing coordinates like [<longitude>,<latitude>]
            client_id: the unique id that is used for the channel api
        """

        # load the options
        options = json.loads(self.request.get("options"))

        # create the chart
        try:
            chart = _GetChart(options)
        except Exception as e:
            if DEBUG:
                _SendMessage(options["client_id"],"chart-" + options["filename"],"danger","Chart creation failed.", str(e) + " - " + traceback.format_exc())
            else:
                _SendMessage(options["client_id"],"chart-" + options["filename"],"danger","Chart creation failed.", str(e))
            return

        # _GetChart returns None if the collection is empty
        if chart is None:
            _SendMessage(options["client_id"],"chart-" + options["filename"],"danger","Chart creation failed.","No images in collection. Change your options.")
            return

        # send the small chart to client
        _SendMessage(options["client_id"],"chart-" + options["filename"],"success","Chart for '" + options["filename"] + "':",chart)


class DownloadHandler(DataHandler):

    """A servlet to handle the download link creation requests"""

    def DoPost(self):
        """Creates a download url (directly from EE) for the region specified in the options.

        HTTP Parameters:
            regression: the regression type [poly1,poly2,poly3,zhuWood]
            source: the source satellite [all,land5,land7,land8]
            start: the start year to filter the satellite images (including)
            end: the end year to filter the satellite images (including)
            cloudscore: the max cloudscore for the ee.Algorithms.Landsat.simpleCloudScore [1-100]
                        Higher means that the pixel is more likley to be a cloud
            region: an array of arrays representing a region [[<longitude>,<latitude>],[<longitude>,<latitude>],...]
            client_id: the unique id that is used for the channel api.

        Returns:
            A dictionary with the key "url" containing the download url
        """
        # read the request options
        options = _ReadOptions(self.request)

        # notify client that the url creation has started
        _SendMessage(options["client_id"],"download-" + options["filename"],"info","Download creation of '" + options["filename"] + "' in progress.")

        # get the image and then the download url from EE
        image = _GetImage(options)

        # _GetImage returns None if the collection is empty
        if image is None:
            return {"error": "No images in collection. Change your options."}

        downloadUrl = image.getDownloadURL({"name":options["filename"],"scale":EXPORT_RESOLUTION,"region":options["region"]})

        # send the url to the client
        _SendMessage(options["client_id"],"download-" + options["filename"],"success","Download link for '" + options["filename"] + "':","<a target='_blank' href='" + downloadUrl + "'>" + downloadUrl + "</a>")

        # returns the download url (response is not used on the client side)
        return {"url":downloadUrl}


class ExportHandler(DataHandler):

    """A servlet to handle requests for image exports."""

    def DoPost(self):
        """Kicks off export of an image for the specified options.

        HTTP Parameters:
            regression: the regression type [poly1,poly2,poly3,zhuWood]
            source: the source satellite [all,land5,land7,land8]
            start: the start year to filter the satellite images (including)
            end: the end year to filter the satellite images (including)
            cloudscore: the max cloudscore for the ee.Algorithms.Landsat.simpleCloudScore [1-100]
                        Higher means that the pixel is more likley to be a cloud
            region: an array of arrays representing a region [[<longitude>,<latitude>],[<longitude>,<latitude>],...]
            client_id: the unique id that is used for the channel api.
        """
        # read the options
        options = _ReadOptions(self.request)

        running_export = memcache.get(options["client_id"])

        # check if the user has an export running
        if running_export is not None and running_export["task"] is not None:
            return {"error":"Currently another export is running for you. Please wait or cancel it."}

        # Kick off an export runner to start and monitor the EE export task.
        # Note: The work "task" is used by both Earth Engine and App Engine to refer
        # to two different things. "TaskQueue" is an async App Engine service.
        # only execute once even if task fails
        taskqueue.add(url="/exportrunner", params={"options":json.dumps(options)}, retry_options=taskqueue.TaskRetryOptions(task_retry_limit=0,task_age_limit=1))

        # notify client that the export has started
        _SendMessage(options["client_id"],"export-" + options["filename"],"info","Export of '" + options["filename"] + "' in progress.")


###############################################################################
#                           The task status poller.                           #
###############################################################################
class ExportRunnerHandler(webapp2.RequestHandler):

    """A servlet for handling async export task requests."""

    def post(self):
        """Exports an image for the given options and provides a 5 hours valid download url.

        This is called by our trusted export handler and runs as a separate
        process. If the deadline of 10 Minutes is exceeded the EE task ID and polling counter
        will be handed over to a new /exportrunner.

        HTTP Parameters:
            regression: the regression type [poly1,poly2,poly3,zhuWood]
            source: the source satellite [all,land5,land7,land8]
            start: the start year to filter the satellite images (including)
            end: the end year to filter the satellite images (including)
            cloudscore: the max cloudscore for the ee.Algorithms.Landsat.simpleCloudScore [1-100]
                        Higher means that the pixel is more likley to be a cloud
            region: an array of arrays representing a region [[<longitude>,<latitude>],[<longitude>,<latitude>],...]
            client_id: the unique id that is used for the channel api.
        """

        # start time in epoch seconds + 9 minutes
        end_time = time.time() + 9*60

        # load the options
        options = json.loads(self.request.get("options"))

        try:
            # reads EE task id and polling counter from an previous /exportrunner
            task_id = self.request.get("task_id", default_value=None)
            try:
                task_count = int(self.request.get("task_count"))
            except ValueError:
                task_count = None


            # task_id and task_count are None if this is a new /exportrunner request
            if task_id is None or task_count is None:

                image = _GetImage(options)

                # _GetImage returns None if the collection is empty
                if image is None:
                    _SendMessage(options["client_id"],"export-" + options["filename"],"danger","Export of '" + options["filename"] + "' failed.","No images in collection. Change your options.")
                    return

                # Determine the geometry based on the polygon's coordinates.
                geometry = ee.Geometry.Polygon(options["region"])

                # cut out the geometry (the client drawn polygon)
                image = image.clip(geometry)

                # Create and start the task.
                task = ee.batch.Export.image(
                        image=image,
                        description=options["filename"],
                        config={
                                "driveFileNamePrefix": options["filename"],
                                "maxPixels": EXPORT_MAX_PIXELS,
                                "scale": EXPORT_RESOLUTION,
                        })
                task.start()
                logging.info("Started EE task (id: %s).", task.id)

                # Temporary save wich client has started wich export task and with which file name.
                # Useed for verification during task cancellation or file deletion.
                # Also used to ensure that a client has only one running export at the same time
                memcache.set(options["client_id"],{"task":task.id,"filename":None})

                task_id = task.id
                task_count = 1


            def getTaskError(task_status):
                if task_status["state"] == ee.batch.Task.State.FAILED:
                    return task_status["error_message"]
                else:
                    return "No error message"

            # Wait for the task to complete.
            counter = task_count

            task_status = ee.data.getTaskStatus(task_id)[0]
            state = task_status["state"]

            while state in (ee.batch.Task.State.READY, ee.batch.Task.State.RUNNING):  # excluded CANCEL_REQUESTED because EE needs to long to cancel a task
                if time.time() >= end_time:
                    logging.info("Handing over task (id: %s).", task_id)
                    # after 9 minutes hand over the task polling to a new /exportrunner because the deadline for tasks is 10 minutes
                    taskqueue.add(url="/exportrunner", params={"options":json.dumps(options),"task_id":task_id,"task_count":counter})
                    return
                logging.info("Polling for task (id: %s).", task_id)

                # sends a cancellation but still alive notification to the client
                if state == ee.batch.Task.State.CANCEL_REQUESTED:
                    _SendMessage(options["client_id"],"export-" + options["filename"],"warning","Cancellation of '" + options["filename"] + "' in progress.","Working since " + str(counter*TASK_POLL_FREQUENCY) + " seconds...")
                else:
                    # sends a alive notification to the client
                    _SendMessage(options["client_id"],"export-" + options["filename"],"info","Export of '" + options["filename"] + "' in progress.","Working since " + str(counter*TASK_POLL_FREQUENCY) + " seconds...<br><br><a href='javascript:;' onclick=\"$('[data-alert-name=\\'export-%s\\']').removeClass('alert-info').addClass('alert-warning');$.get('/clean?task=%s&client_id=%s');\">Cancel this export</a>" % (options["filename"],task_id,options["client_id"]))

                time.sleep(TASK_POLL_FREQUENCY)

                counter = counter + 1

                task_status = ee.data.getTaskStatus(task_id)[0]
                state = task_status["state"]

            # Checks if the task succeeded and if so sends the download url to the client
            if state == ee.batch.Task.State.COMPLETED:
                logging.info("Task succeeded (id: %s).", task_id)
                try:

                    files = DRIVE_HELPER.GetExportedFiles(options["filename"])

                    # Checks if some files were found (sometimes this seems to happen to fast and no files are found although they are there)
                    if len(files) < 1:
                        raise Exception("Cloud not find file: " + options["filename"])

                    urls = []
                    for f in files:
                        urls.append({"url":DRIVE_HELPER.GetDownloadUrl(f["id"]),"title": f["title"],"id":f["id"]})

                    # If the export area is large EE will create mutliple files, then this code will return a url to a google drive folder and a download url for each file
                    if len(urls) == 1:
                        line2 = "<a target='_blank' href='" + urls[0]["url"] + "'>Download via Google Drive (valid for 5 hours)</a>"
                        del_message = "Delete this file"
                    else:
                        folder_id = DRIVE_HELPER.CreatePublicFolder(options["filename"])
                        i = 1
                        line2 = "<a target='_blank' href='https://drive.google.com/folderview?id=" + folder_id + "'>Open in Google Drive (valid for 5 hours)</a>"
                        for url in urls:
                            DRIVE_HELPER.RenameFile(url["id"],options["filename"] + "_part_%s.tif" % i)
                            DRIVE_HELPER.MoveFileToFolder(url["id"],folder_id)
                            line2 = line2 + "<br><a target='_blank' href='https://docs.google.com/uc?id=%s&export=download'>Download part %s</a>" % (url["id"],i)
                            i = i + 1
                        del_message = "Delete these files"

                    # add deletion link
                    line2 = line2 + "<br><br><a href='javascript:;' onclick=\"$('[data-alert-name=\\'export-%s\\']').removeClass('alert-success').addClass('alert-warning');$.get('/clean?filename=%s&client_id=%s');\">%s</a>" % (options["filename"],options["filename"],options["client_id"],del_message)

                    # Update the memcache entry with the filename and clear the task id
                    memcache.set(options["client_id"],{"task":None,"filename":options["filename"]})

                    # Notify the user's browser that the export is complete.
                    _SendMessage(options["client_id"],"export-" + options["filename"],"success","Export of '" + options["filename"] + "' complete.", line2)
                except Exception as e:
                    if DEBUG:
                        line2 = str(e) + " - " + traceback.format_exc()
                    else:
                        line2 = str(e)

                    memcache.set(options["client_id"],None)
                    _SendMessage(options["client_id"],"export-" + options["filename"],"danger","Export of '" + options["filename"] + "' failed.", line2)

            # Note: Notify client already if state is CANCEL_REQUESTED because EE needs to long to cancel the task
            elif state == ee.batch.Task.State.CANCELLED or state == ee.batch.Task.State.CANCEL_REQUESTED:
                memcache.set(options["client_id"],None)
                _SendMessage(options["client_id"],"export-" + options["filename"],"warning","Export of '" + options["filename"] + "' cancelled.")
            else:
                memcache.set(options["client_id"],None)
                _SendMessage(options["client_id"],"export-" + options["filename"],"danger","Export of '" + options["filename"] + "' failed.","Task %s (id: %s).<br>%s" % (state,task_id,getTaskError(task_status)))
        except Exception as e:
            if DEBUG:
                _SendMessage(options["client_id"],"export-" + options["filename"],"danger","Export of '" + options["filename"] + "' failed.", str(e) + " - " + traceback.format_exc())
            else:
                _SendMessage(options["client_id"],"export-" + options["filename"],"danger","Export of '" + options["filename"] + "' failed.", str(e))
            return


class ChannelCloseHandler(webapp2.RequestHandler):

    """Handler that cancels an open export task if the client closes the channel (usually on page closing)"""

    def post(self):
        """Reads the task id from memcache and calls the /clean?task handler"""
        client_id = self.request.get("from")

        running_export = memcache.get(client_id)

        if running_export is not None and running_export["task"] is not None:
            urlfetch.fetch("/clean?task=%s&client_id=%s" % (running_export["task"],client_id))


class CleanHandler(DataHandler):

    """A servlet for the cron job that runs every hour.
        It deletes all files older than 5 hours from the service Google Drive account.
    """

    def cancelTask(self,client_id,task_id=None):
        """Cancels the running EE task form the client if there is one

        Args:
            client_id: the Channel API client id
            task_id: The task id that should be cancelled. If None the task id that is saved in memcache ist used

        """
        running_export = memcache.get(client_id)

        if running_export is not None:
            if task_id is not None and running_export["task"] == task_id:
                ee.data.cancelTask(task_id)
                logging.info("Cancelled task (id: %s).", task_id)
            elif task_id is None and running_export["task"] is not None:
                ee.data.cancelTask(running_export["task"])
                memcache.set(client_id,running_export)


    def DoPost(self):
        """Handels the Channel disconnected request. Deletes the running task for the client.

        HTTP Parameters:
            from: the client_id that disconnected
        """
        # TODO migrate this to firebase to delete data from no longer connected clients
        self.cancelTask(self.request.get("from"))


    def DoGet(self):
        """Deletes each file in the service Google Drive account that is older than 5 hours (only for admins).
            Or behaves corresponding to the given parameters.

        HTTP Parameters:
            task: A EE task Id that should be cancelled
            filename: Filename of an export to delete these files from the service Google Drive
            client_id: The client_id with which the task or export files are created to verify the ownership
            m: Switches the handlers mode. (only for admins)
                If m = "view" it will show all files that are in the service Google Drive account and the free space in MB.
                If m = "all" it will delete all files that are in the service Google Drive account.
        """
        user = users.get_current_user()

        task = self.request.get("task", default_value=None)
        filename = self.request.get("filename", default_value=None)
        client_id = self.request.get("client_id", default_value=None)

        m = self.request.get("m", default_value=None)


        # Cancels a specific EE export task
        if task is not None and client_id is not None:
            self.cancelTask(client_id,task)


        # Deletes all files from an specific export
        elif filename is not None and client_id is not None:
            last_export = memcache.get(client_id)

            if last_export is not None and last_export["filename"] == filename:
                for f in DRIVE_HELPER.GetExportedFiles(filename):
                    DRIVE_HELPER.DeleteFile(f["id"])
                    logging.info("Deleted File: %s - %s" % (f["title"],f["id"]))

                _SendMessage(client_id,"export-" + filename,"success","File deletion for '" + filename + "' complete.")


        # If m is set user must be admin to access the view and delete all function
        elif m is not None and users.is_current_user_admin():

            # shows all files
            if m == "view":
                out = {}
                files = []
                for f in DRIVE_HELPER.GetExportedFiles(None):
                    if "fileSize" in f:
                        files.append({"type":"file","title": f["title"],"id":f["id"],"createdDate":f["createdDate"],"fileSizeMB":int(f["fileSize"])/1024/1024})
                    else:
                        files.append({"type":"folder","title": f["title"],"id":f["id"],"createdDate":f["createdDate"]})

                about = DRIVE_HELPER.service.about().get().execute()
                free = int(about["quotaBytesTotal"]) - int(about["quotaBytesUsed"])

                out["files"] = files
                out["freeSpaceMB"] = free/1024/1024
                return out

            # deletes all files
            elif m == "all":
                files = DRIVE_HELPER.GetExportedFiles(None)
                for f in files:
                    DRIVE_HELPER.DeleteFile(f["id"])
                    logging.info("Deleted File: %s - %s" % (f["title"],f["id"]))
            else:
                return {"error": "Invalid value for parameter 'm'."}


        # Deletes all files older than 5 hours
        # check if user is admin or if call comes from cron job
        elif (urlparse.urlsplit(self.request.url).path.startswith("/cron/clean") and user is None) or users.is_current_user_admin():

            files = DRIVE_HELPER.GetExportedFiles(None)
            for f in files:
                file_date = datetime.strptime(f["createdDate"].split(".")[0],"%Y-%m-%dT%H:%M:%S")
                diff_seconds = (datetime.utcnow() - file_date).total_seconds()

                if diff_seconds > 5*60*60:
                    DRIVE_HELPER.DeleteFile(f["id"])
                    logging.info("Deleted File: %s - %s" % (f["title"],f["id"]))
        else:
            self.response.set_status(403)
            self.response.headers["Content-Type"] = "text/html; charset=utf-8"
            self.response.out.write("<html><body>You need to be an admin.<br><a href='%s'>Login here</a></body></html>" % users.create_login_url(dest_url=self.request.url))


###############################################################################
#                                   Helpers.                                  #
###############################################################################

def _ReadOptions(request):
    """Reads the option values from a request
    Args:
        request: a request object
    Returns:
        A dict with all option values
    """
    options = {}
    options["regression"] = request.get("regression")
    options["source"] = request.get("source")
    options["start"] = int(request.get("start"))
    options["end"] = int(request.get("end"))
    options["cloudscore"] = int(request.get("cloudscore"))
    options["point"] = json.loads(request.get("point"))
    options["region"] = json.loads(request.get("region"))
    options["filename"] = request.get("filename")
    options["client_id"] = request.get("client_id")

    logging.info("Received options: " + json.dumps(options))

    # TODO logic checking

    return options


def _GetCollection(options,point=True,region=True):
    """Creates a ee.ImageCollection with the given options. Also the ee.Algorithms.Landsat.simpleCloudScore is used
        on each image with the cloudscore from the options and the bands are reduced and renamed to RED and NIR.
    Args:
        options: a dict created by _ReadOptions()
        point: boolean if the point coordinates should be used to locate the ImageCollection
        region: boolean if the region coordinates should be used to locate the ImageCollection
    Returns:
        A ee.ImageCollection where each image has 2 bands RED and NIR and is cloudscore masked or None if collection is empty.
    """

    # rename the used option values
    source = options["source"]
    start = options["start"]
    end = options["end"]
    cloudscore = options["cloudscore"]
    if point:
        point = options["point"]
    else:
        point = None
    if region:
        region = options["region"]
    else:
        region = None
    client_id = options["client_id"]

    # the names for the different top of atmosphere satellite images
    sourceSwitch = {"land5": "LANDSAT/LT5_L1T_TOA", "land7": "LANDSAT/LE7_L1T_TOA", "land8": "LANDSAT/LC8_L1T_TOA"}
    bandPattern = {"land5": ["B3","B4"], "land7": ["B3","B4"], "land8": ["B4","B5"]}  # to rename bands for ndvi calculation

    # This function masks the input with a threshold on the simple cloud score.
    def cloudMask(img):
        cloud = ee.Algorithms.Landsat.simpleCloudScore(img).select("cloud")
        return img.updateMask(cloud.lt(cloudscore))

    # Reduce a collection to a specific region or point (or both)
    def filterRegions(collection,point,region):
        if region is None and point is not None:
            return collection.filterBounds(ee.Geometry.Point(point))
        elif region is not None and point is None:
            return collection.filterBounds(ee.Geometry.Polygon(region))
        elif region is not None and point is not None:
            c1 = collection.filterBounds(ee.Geometry.Point(point))
            c2 = collection.filterBounds(ee.Geometry.Polygon(region))

            c3 = ee.ImageCollection(c1.merge(c2))  # merge the collections

            return ee.ImageCollection(c3.distinct(ee.SelectorSet("LANDSAT_SCENE_ID")))  # sort out double selected
        else:
            raise Exception("No location selected")


    collection_line2 = None  # line2 of the information about the collection returned over the channel api

    # If source is all a collection for each satellite is created
    if source == "all":
        # select only the images that were took between start and end
        land5 = ee.ImageCollection(sourceSwitch["land5"]).filterDate(str(start) + "-01-01", str(end) + "-12-31T23:59:59")
        land7 = ee.ImageCollection(sourceSwitch["land7"]).filterDate(str(start) + "-01-01", str(end) + "-12-31T23:59:59")
        land8 = ee.ImageCollection(sourceSwitch["land8"]).filterDate(str(start) + "-01-01", str(end) + "-12-31T23:59:59")

        # only select the images that intersect with the coordinates of point or region
        land5 = filterRegions(land5,point,region)
        land7 = filterRegions(land7,point,region)
        land8 = filterRegions(land8,point,region)

        # get the number of images in each collection
        land5_size = land5.size().getInfo()
        land7_size = land7.size().getInfo()
        land8_size = land8.size().getInfo()
        collection_line2 = "Landsat 5: %s<br>Landsat 7: %s<br>Landsat 8: %s" % (land5_size,land7_size,land8_size)

        # use the simpleCloudScore algorithm on each collection
        if cloudscore > 0 and cloudscore < 100:
            land5 = land5.map(cloudMask)
            land7 = land7.map(cloudMask)
            land8 = land8.map(cloudMask)

        # select only the RED and the NIR band
        land5 = land5.select(bandPattern["land5"],["RED","NIR"])
        land7 = land7.select(bandPattern["land7"],["RED","NIR"])
        land8 = land8.select(bandPattern["land8"],["RED","NIR"])

        # merge the 3 collections
        collection = ee.ImageCollection(land5.merge(land7))
        collection = ee.ImageCollection(collection.merge(land8))
    else:
        # select only the images that were took between start and end
        collection = ee.ImageCollection(sourceSwitch[source]).filterDate(str(start) + "-01-01", str(end) + "-12-31T23:59:59")

        # only select the images that intersect with the coordinates of point or region
        collection = filterRegions(collection,point,region)

        # use the simpleCloudScore algorithm
        if cloudscore > 0 and cloudscore < 100:
            collection = collection.map(cloudMask)

        # select only the RED and the NIR band
        collection = collection.select(bandPattern[source],["RED","NIR"])


    # Check if the collection conatins images if not return none
    collection_size = collection.size().getInfo()
    if collection_size == 0:
        return None

    # send number of images over Channel API to client
    _SendMessage(client_id,"collection-info","info","Your collection contains %s images." % collection_size, collection_line2)

    return collection


def _GetChart(options):
    """Generates html code for a small chart and prepares the creation of a full sceen view by saving
        the chart options under a unique id in the Memcache.
    Args:
        options: a option dic created by _ReadOptions()
    Returns:
        Html code with the small chart view or None if collection is empty.
    """
    regression = options["regression"]
    point = options["point"]
    start = options["start"]
    end = options["end"]

    collection = _GetCollection(options,region=False)  # only use point to filter region

    # _GetCollection() returns None if collection is empty
    if collection is None:
        return None

    # Generates an image with a band "nd" that contains the NDVI
    # and a band "system:time_start" that contains the creation date of the image as seconds since epoch
    def calcValues(img):
        return (img.select()
                .addBands(img.metadata("system:time_start").divide(1000).floor())  # convert to seconds
                .addBands(img.normalizedDifference(["NIR","RED"])))  # NDVI

    collection = collection.map(calcValues)

    # Extracts the pixel values at a specific point and adds them as array clalled "vlaues" to the image properties
    def getValues(img):
        # useing that the mean reducer only got one value because the poi_geometry is just a point
        return img.reduceRegions(ee.Geometry.Point(point), ee.Reducer.mean(),EXPORT_RESOLUTION).makeArray(["system:time_start","nd"],"values")

    # Creates a list of arrays like [[<image1 epoch seconds>,<image1 ndvi>],[<image2 epoch seconds>,<image2 ndvi>],...]
    # aggregate_array also filters the masked pixels out
    raw_data = ee.FeatureCollection(collection.map(getValues)).flatten().aggregate_array("values").getInfo()


    # style information for the different chart types
    if regression == "zhuWood":

        # get the regression coefficients at the point of interest (makes chart creation a lot slower)
        image = _GetImage(options)
        coeff = image.reduceRegion(ee.Reducer.mean(),ee.Geometry.Point(point),EXPORT_RESOLUTION).getInfo()

        coeff_map = {"a0":coeff["a0_sec"],"a1":coeff["a1_sec"],"a2":coeff["a2_sec"],"a3":coeff["a3_sec"],"rmse":coeff["rmse"]}
        # describe xAxis and yAxis
        description = [("Date","date"),("NDVI", "number"),("Regression: a0=%(a0)s, a1=%(a1)s, a2=%(a2)s, a3=%(a3)s, rmse=%(rmse)s" % coeff_map,"number")]

        hAxis = """{title:"Date"},"""
        chartArea = "{width: \"75%\"}"
        per = "Date"

        # start and end epoch seconds of the collection
        seconds_start = calendar.timegm(time.strptime("%s-01-01" % start, "%Y-%m-%d"))
        seconds_end = calendar.timegm(time.strptime("%s-12-31T23:59:59" % end, "%Y-%m-%dT%H:%M:%S"))

        # convert raw_data to data
        data = []
        for x in raw_data:
            seconds = x[0]
            ndvi = x[1]

            # convert epoch seconds to datetime object
            # not using the seconds because the Google Visualization API can display dates nicely
            data.append([datetime.utcfromtimestamp(seconds),ndvi,None])


        # calculate and add the values of the regression every 45 days
        for x in range(seconds_start,seconds_end,45*24*60*60):
            offset = x - seconds_start

            # calculate the regression ndvi value
            reg_ndvi = coeff["a0_sec"] + coeff["a1_sec"] * math.cos((2*math.pi/(365*24*60*60))*offset) + coeff["a2_sec"] * math.sin((2*math.pi/(365*24*60*60))*offset) + coeff["a3_sec"] * offset

            # convert time_struct to datetime and add it with the regression value to the data
            data.append([datetime(*time.gmtime(x)[:6]),None,reg_ndvi])

        trendline = """legend:{position:"bottom"},series:{1:{lineWidth: 1}},"""
    else:
        hAxis = """{title:"DOY",minValue:0,maxValue:365},"""
        chartArea = "{width: \"50%\"}"
        per = "DOY"

        # is for all points to display the regression (0_ prefix so it is always the first)
        reg_name = "0_%s" % regression
        yAxis = {reg_name:"number"}
        for year in range(start,end + 1):
            # add yAxis description per year
            yAxis[str(year)] = "number"

        # DataTable description
        description = {("DOY","number"): yAxis}

        data = {}
        for x in raw_data:
            # converts epoch seconds to day of year
            date = datetime.utcfromtimestamp(x[0])
            doy = date.timetuple().tm_yday

            year = str(date.timetuple().tm_year)

            data[doy] = {reg_name:x[1],year:x[1]}

        degree = {"poly1":1,"poly2":2,"poly3":3}
        # hide dataset that holds all points and only display the regression for it
        trendline = """series:{0:{visibleInLegend: false}},trendlines:{0:{type:"polynomial",degree:%s,showR2: true, visibleInLegend: true}},""" % degree[regression]



    # Create the DataTable and load the data into it
    # more details about the Google Visualization API at https://developers.google.com/chart/interactive/docs/reference
    data_table = gviz_api.DataTable(description)
    data_table.LoadData(data)

    # Creating a JavaScript code string that represents the chart
    jscode = data_table.ToJSCode("data")

    # Create temporary chart id
    chart_id = _GetUniqueString()

    # Set request options as chart options, and add some extra values
    chart_options = options.copy()
    chart_options.update({"jscode":jscode,"lat":point[1],"lon":point[0],"trendline":trendline,"hAxis":hAxis,"chart_id":chart_id,"chartArea":chartArea,"per":per})

    # Save the chart options temporary in Memcache
    memcache.set(chart_id,chart_options)

    if len(jscode) < 31000:  # max 32767 chars per channel api message
        # Load small chart template
        f = open("templates/small_chart.html", "r")
        small_chart = f.read()
        f.close()

        # Fill in chart options an return template
        return small_chart % chart_options
    else:
        return """No small chart available.<br><a href="/chart?id=%(chart_id)s" target="_blank">Full screen url (only temporary valid)</a>""" % chart_options


def _GetImage(options):
    """Returns the ndvi regression image for the given options.

    Args:
        options: a dict created by _ReadOptions() containing the request options

    Returns:
        An ee.Image with the coefficients of the regression and a band called "rmse" containing the
        Root Mean Square Error for the ndvi value calculated by the regression or None if collection is empty.
    """

    # renaming the used options
    regression = options["regression"]
    start = options["start"]

    collection = _GetCollection(options)

    # _GetCollection() returns None if collection is empty
    if collection is None:
        return None

    # Function to calculate the values needed for a regression with a polynomial of degree 1
    def makePoly1Variables(img):
        date = img.date()
        doy = date.getRelative("day", "year")

        x1 = doy
        x0 = 1

        return (img.select()
                .addBands(ee.Image.constant(x0))                    # 0. a0 constant term
                .addBands(ee.Image.constant(x1))                    # 1. a1*x
                .addBands(img.normalizedDifference(["NIR","RED"]))  # 2. response variable (NDVI)
                .toFloat())

    # Function to calculate the values needed for a regression with a polynomial of degree 2
    def makePoly2Variables(img):
        date = img.date()
        doy = date.getRelative("day", "year")

        x2 = doy.pow(2)
        x1 = doy
        x0 = 1

        return (img.select()
                .addBands(ee.Image.constant(x0))                    # 0. a0 constant term
                .addBands(ee.Image.constant(x1))                    # 1. a1*x
                .addBands(ee.Image.constant(x2))                    # 2. a2*x^2
                .addBands(img.normalizedDifference(["NIR","RED"]))  # 4. response variable (NDVI)
                .toFloat())

    # Function to calculate the values needed for a regression with a polynomial of degree 3
    def makePoly3Variables(img):
        date = img.date()
        doy = date.getRelative("day", "year")

        x3 = doy.pow(3)
        x2 = doy.pow(2)
        x1 = doy
        x0 = 1

        return (img.select()
                .addBands(ee.Image.constant(x0))                    # 0. a0 constant term
                .addBands(ee.Image.constant(x1))                    # 1. a1*x
                .addBands(ee.Image.constant(x2))                    # 2. a2*x^2
                .addBands(ee.Image.constant(x3))                    # 3. a3*x^3
                .addBands(img.normalizedDifference(["NIR","RED"]))  # 4. response variable (NDVI)
                .toFloat())

    # Function to calculate the values needed for a regression with the model after Zhu & Woodcock
    def makeZhuWoodVariables(img):

        seconds = img.date().millis().divide(1000).floor()
        seconds_start = ee.Date("%s-01-01" % start).millis().divide(1000).floor()
        seconds_offset = seconds.subtract(seconds_start)

        sin_intra = ee.Number(2).multiply(math.pi).divide(365*24*60*60).multiply(seconds_offset).sin()
        cos_intra = ee.Number(2).multiply(math.pi).divide(365*24*60*60).multiply(seconds_offset).cos()
        inter = seconds_offset

        return (img.select()
                .addBands(ee.Image.constant(1))                     # 0. constant term
                .addBands(ee.Image.constant(cos_intra))             # 1. cos intra-annual
                .addBands(ee.Image.constant(sin_intra))             # 2. sin intra-annual
                .addBands(ee.Image.constant(inter))                 # 3. inter-annual
                .addBands(img.normalizedDifference(["NIR","RED"]))  # 5. response variable (NDVI)
                .toFloat())

    makeVariables = {"poly1": makePoly1Variables,"poly2": makePoly2Variables, "poly3": makePoly3Variables, "zhuWood": makeZhuWoodVariables}

    # calculate the needed values for the regression
    collection_prepared = collection.map(makeVariables[regression])

    predictorsCount = {"poly1": 2,"poly2": 3, "poly3": 4, "zhuWood": 4}

    # counts the ndvi values per pixel
    countValues = collection_prepared.select("nd").reduce(ee.Reducer.count())

    # masks pixels with less than 2 * number of predictors, to deliver better results
    def countMask(img):
        return img.updateMask(countValues.gt(predictorsCount[regression]*2-1))

    # use the countMask
    collection_prepared = collection_prepared.map(countMask)

    # doing the regression
    coefficients = collection_prepared.reduce(ee.Reducer.linearRegression(predictorsCount[regression], 1))

    # flattens regression coefficients to one image with multiple bands
    flattenPattern = {"poly1": ["a0", "a1"], "poly2": ["a0", "a1", "a2"], "poly3": ["a0", "a1", "a2", "a3"], "zhuWood": ["a0", "a1", "a2", "a3"]}
    renamePattern = {"poly1": "doy", "poly2": "doy", "poly3": "doy", "zhuWood": "sec"}
    coefficientsImage = coefficients.select(["coefficients"]).arrayFlatten([flattenPattern[regression],[renamePattern[regression]]])

    # flattens the root mean square of the predicted ndvi values
    rmse = coefficients.select("residuals").arrayFlatten([["rmse"]])

    # combines coefficients and rmse and returns them a one ee.Image
    return coefficientsImage.addBands(rmse)


def _GetUniqueString():
    """Returns a likely-to-be unique string."""
    random_str = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    date_str = str(int(time.time()))
    return date_str + random_str


def _SendMessage(client_id, id, style, line1, line2=None):
    """Sends messages to the client over the Channel API

    Args:
        client_id: the clients channel api id
        id: id of the alert
        style: type of the alert for Bootstrap CSS styling
        line1: The first line of the alert text
        line2: optinal second line of the alert text
    """
    params = {"id": id, "style": style, "line1": line1}

    if line2 is not None:
        params["line2"] = line2

    logging.info("Sent to client: " + json.dumps(params))
    send_firebase_message(client_id, json.dumps(params))


###############################################################################
#                         Firebase helper function.                           #
###############################################################################
# Source: https://github.com/GoogleCloudPlatform/python-docs-samples/blob/master/appengine/standard/firebase/firetactoe/firetactoe.py

def firebase_init():
    """Init the firebase_admin lib"""
    firebase_creds = firebase_admin.credentials.Certificate(config.SERVICE_ACC_JSON_KEYFILE)
    # Initialize the app with a service account, granting admin privileges
    firebase_admin.initialize_app(firebase_creds, {"databaseURL": FIREBASE_DB_URL})


def get_firebase_db_url():
    """Grabs the databaseURL from the Firebase config snippet. Regex looks
    scary, but all it is doing is pulling the 'databaseURL' field from the
    Firebase javascript snippet"""
    regex = re.compile(r'\bdatabaseURL\b.*?["\']([^"\']+)')
    cwd = os.path.dirname(__file__)
    try:
        with open(os.path.join(cwd, 'templates', config.FIREBASE_CONFIG)) as f:
            url = next(regex.search(line) for line in f if regex.search(line))
    except StopIteration:
        raise ValueError(
            'Error parsing databaseURL. Please copy Firebase web snippet '
            'into templates/{}'.format(config.FIREBASE_CONFIG))
    return url.group(1)


# Need to use own Http object because free app engine does not allow use of requests lib which is used by firebase_admin
def get_firebase_http():
    """Provides an authed http object."""
    http = httplib2.Http()
    CREDENTIALS.authorize(http)
    return http


def send_firebase_message(uid, message=None):
    """Updates data in firebase. If a message is provided, then it updates
     the data at /channels/<channel_id> with the message using the PATCH
     http method. If no message is provided, then the data at this location
     is deleted using the DELETE http method
     """
    url = '{}/channels/{}.json'.format(FIREBASE_DB_URL, uid)

    if message:
        return FIREBASE_HTTP.request(url, 'PATCH', body=message)
    else:
        return FIREBASE_HTTP.request(url, 'DELETE')


# This function can only be used in a paid App Engine (because it requiers the requests lib)
# def send_firebase_message(uid, message=None):
#     channel = firebase_db.reference("channels/%s" % uid)

#     if message:
#         channel.set(message)
#     else:
#         channel.delete()


# luckily firebase_admin.auth does not need requests so we can use the in house funcion to create tokens
def create_custom_token(uid):
    return firebase_auth.create_custom_token(uid)


###############################################################################
#                           App setup/Routing table.                          #
###############################################################################
# firebase setup
FIREBASE_HTTP = get_firebase_http()
FIREBASE_DB_URL = get_firebase_db_url()
firebase_init()  # this initializes firebase_admin which is only used for token generation (because of requests limitation in free app engine)

# The webapp2 routing table from URL paths to web request handlers. See:
# http://webapp-improved.appspot.com/tutorials/quickstart.html
app = webapp2.WSGIApplication([
        ("/download", DownloadHandler),
        ("/chart", ChartHandler),
        ("/chartrunner", ChartRunnerHandler),
        ("/export", ExportHandler),
        ("/exportrunner", ExportRunnerHandler),
        ("/cron/clean", CleanHandler),
        ("/clean", CleanHandler),
        ("/mapid", MapIdHandler),
        ("/", MapHandler),
])
