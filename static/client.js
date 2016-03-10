/**
 * @fileoverview Runs the application. The code is executed in
 * the user's browser. It communicates with the App Engine backend, renders
 * output to the screen, and handles user interactions.
 */

ntst = {};  // Our namespace (NDVI Time Series Tool)

/**
 * Starts the application. The main entry point for the app.
 * @param {string} channelToken The token used for Channel API communication
 *     with the App Engine backend.
 * @param {string} clientId The ID of this client for the Channel API.
 */
ntst.boot = function(channelToken, clientId) {
  var app = new ntst.App(channelToken, clientId);
};


///////////////////////////////////////////////////////////////////////////////
//                               The application.                            //
///////////////////////////////////////////////////////////////////////////////

/**
 * The main application.
 * This constructor renders the UI and sets up event handling.
 * @param {string} channelToken The token used for Channel API communication
 *     with the App Engine backend.
 * @param {string} clientId The ID of this client for the Channel API.
 * @constructor
 */
ntst.App = function(channelToken, clientId) {
  // The Google Map.
  this.map = ntst.App.createMap($(".map").get(0));

  // The drawing manager, for drawing on the Google Map.
  this.drawingManager = ntst.App.createDrawingManager(this.map);

  // Outstanding map ID requests, keyed by layer name.
  // Used to cancel no-longer-needed requests if the user changes dates before
  // an outstanding map ID is returned.
  this.layerRequests = {};

  // Outstanding URL paths for the current map IDs, keyed by layer name.
  // Used to avoid needlessly changing the layer when the layer requested is the
  // the same as the current layer.
  this.layerPaths = {};

  // Holds the MapLayerOverlay objects for the different image bands
  // to switch between them.
  this.layerBands = {};

  // The ID of this client for socket communication with App Engine.
  this.clientId = clientId;

  // The channel used for communication with our App Engine backend.
  this.channel = new goog.appengine.Channel(channelToken);

  // Initialize the UI components.
  this.initOptions();
  this.initRegionPicker();
  this.initMarkerPicker();
  this.initAlerts();
};


///////////////////////////////////////////////////////////////////////////////
//                               Static values                               //
///////////////////////////////////////////////////////////////////////////////

/** @type {string} The Earth Engine API URL. */
ntst.App.EE_URL = "https://earthengine.googleapis.com";

/** @type {number} The default zoom level for the map. */
ntst.App.DEFAULT_ZOOM = 5;

/** @type {Object} The default center of the map. */
ntst.App.DEFAULT_CENTER = {lng: 10.32, lat: 53.95};


///////////////////////////////////////////////////////////////////////////////
//                               Option Helpers                              //
///////////////////////////////////////////////////////////////////////////////

/**
 * Initializes the option gui elements.
 */
ntst.App.prototype.initOptions = function() {
  // Create the date pickers.
  $(".start-picker, .end-picker").datepicker({
    format: "yyyy",
    viewMode: "years",
    minViewMode: "years",
    autoclose: true,
    startDate: new Date("1984"),
    endDate: new Date()
  });

  // set min/max Dates of datepickers
  $(".start-picker").change(function(){
    $(".end-picker").datepicker("setStartDate",new Date($(".start-picker").val()));
  });
  $(".end-picker").change(function(){
    $(".start-picker").datepicker("setEndDate",new Date($(".end-picker").val()));
  });

  // hide the options
  $("#closeOptions").click(function(){
    $(".panel").css("display","none");
    $(".ui").css("top","40px");
    $("#closedUi").css("display","inline");
  });

  // display the options
  $("#closedUi").click(function(){
    $(".panel").css("display","inline-block");
    $(".ui").css("display","inline");
    $(".ui").css("top","10px");
    $("#closedUi").css("display","none");
    $("#about").css("display","none");
  });

  // show about section
  $("#showAbout").click(function(){
    $(".ui").css("display","none");
    $("#closedUi").css("display","inline");
    $("#about").css("display","inline");
  });

  // init the regression & data picker
  $(".regression-picker, .source-picker").selectpicker({
    width: "auto"
  });

  // Respond when the user updates the source.
  // Set the min/max dates of the datepickers corresponding to the selected source satellite
  $(".source-picker").change((function(){
    var selected = $(".source-picker option:selected").val();

    if(selected == "all"){
      $(".start-picker, .end-picker").datepicker("setStartDate",new Date("1984"));
      $(".start-picker, .end-picker").datepicker("setEndDate",new Date());
    }else if(selected == "land5"){
      $(".start-picker, .end-picker").datepicker("setStartDate",new Date("1984"));
      $(".start-picker, .end-picker").datepicker("setEndDate",new Date("2012"));

      if(parseInt($(".start-picker").val()) > 2012){
        $(".start-picker").datepicker("update", "2012");
      }
      if(parseInt($(".end-picker").val()) > 2012){
        $(".end-picker").datepicker("update", "2012");
      }
    }else if(selected == "land7"){
      $(".start-picker, .end-picker").datepicker("setStartDate",new Date("1999"));
      $(".start-picker, .end-picker").datepicker("setEndDate",new Date());

      if(parseInt($(".start-picker").val()) < 1999){
        $(".start-picker").datepicker("update", "1999");
      }
      if(parseInt($(".end-picker").val()) < 1999){
        $(".end-picker").datepicker("update", "1999");
      }
    }else if(selected == "land8"){
      $(".start-picker, .end-picker").datepicker("setStartDate",new Date("2013"));
      $(".start-picker, .end-picker").datepicker("setEndDate",new Date());

      if(parseInt($(".start-picker").val()) < 2013){
        $(".start-picker").datepicker("update", "2013");
      }
      if(parseInt($(".end-picker").val()) < 2013){
        $(".end-picker").datepicker("update", "2013");
      }
    }
  }).bind(this));
  
  // check min/max values of cloudscore input
  $(".cloudscore-picker").change(function(){
    var value = parseInt($(".cloudscore-picker").val());

    if(value < 1){
      $(".cloudscore-picker").val(1);
    }else if(value > 100){
      $(".cloudscore-picker").val(100);
    }
  });

  //update marker on edit
  $(".lat-picker, .lon-picker").keyup((function(){
    if($(".lat-picker").val() != "" && $(".lon-picker").val()){
      this.setMarker($(".lat-picker").val(),$(".lon-picker").val());
    }
  }).bind(this));

  //update default filename if options change
  $(".regression-picker, .source-picker, .start-picker, .end-picker, .cloudscore-picker").change((function(){
    var filename = this.getOptions().filename;
    filename = filename.replace(/[0-9]{14}/g, "<timestamp>"); //replace timestamp with timestamp placeholder
    $(".filename :text").attr("placeholder",filename);
  }).bind(this));

  // set default values for the datepickers
  $(".start-picker").datepicker("update", "2010");
  $(".end-picker").datepicker("update", "2012");

  // initializes the tooltips
  $("[data-toggle='tooltip']").tooltip({html:true});
  // change the placement of the regression tooltip
  $("#regression-tooltip").tooltip({placement : "bottom"});

  // set compute button action (refresh map)
  $(".compute").click((function(){
    this.removeAlert("collection-info");
    this.refreshImage();
  }).bind(this));

  // set chart button action
  $(".chart").click((function(){
    this.removeAlert("collection-info");
    this.getChart();
  }).bind(this));

  // set export button action
  $(".export").click((function(){
    this.removeAlert("collection-info");
    this.exportImage();
    //this.getDownloadUrl(); // alternative export method
  }).bind(this));

  // initializes the instructions toggle
  $("#toggleInstructions").click(function(){
    if($("#toggleInstructions").html() == "more"){
      $("#instructions2").css("display","inline");
      $("#toggleInstructions").html("less");

      // change the placement of the regression tooltip
      $("#regression-tooltip").tooltip({placement : "top"});
    }else{
      $("#instructions2").css("display","none");
      $("#toggleInstructions").html("more");

      // change the placement of the regression tooltip
      $("#regression-tooltip").tooltip({placement : "bottom"});
    }
  });
};

/**
 * Returns the currently selected options.
 * @return {Object} The current options in a dictionary.
 */
ntst.App.prototype.getOptions = function() {
  var options = {};
  options.regression = $(".regression-picker option:selected").val();
  options.source = $(".source-picker option:selected").val();
  options.start = parseInt($(".start-picker").val());
  options.end = parseInt($(".end-picker").val());
  options.cloudscore = parseInt($(".cloudscore-picker").val());
  options.point = JSON.stringify(this.getMarkerCoordinates());
  options.region = JSON.stringify(this.getPolygonCoordinates());
  options.client_id = this.clientId;

  var userProvidedFilename = $(".filename :text").val();
  if(userProvidedFilename){
    options.filename = userProvidedFilename;
  }else{
    options.filename = "NTST_" + options.regression + "_" + options.source + "_" +
                        options.start + "_" + options.end + "_" + options.cloudscore + "_" + (new Date()).toISOString().replace(/[^0-9]/g, "").substring(0,14);
  }
  return options;
};

/**
 * Returns the currently selected options as String.
 * @param {boolean} if false only the values are connected else the string is human readable
 * @return {String} The current options values connected to one string.
 */
ntst.App.prototype.getOptionsString = function(pretty) {
  var options = this.getOptions();

  var options_string = "";

  if(pretty){
    $.each(options,function(key,value){
      options_string += "{" + key + ":" + value + "}, ";
    });
    options_string = options_string.substring(0,options_string.length - 2);
  }else{
    $.each(options,function(key,value){
      options_string += value;
    });
  }
  return options_string;
};


///////////////////////////////////////////////////////////////////////////////
//                           Marker selection.                               //
///////////////////////////////////////////////////////////////////////////////

/**
* Initializes the marker picker.
*/
ntst.App.prototype.initMarkerPicker = function() {
  // Respond when the user chooses to draw a polygon.
  $(".point .draw").click(this.setMarkerModeEnabled.bind(this, true));

  // keyboard shortcuts
  $(document).keydown((function(event) {
    // Cancel drawing mode if the user presses escape.
    if (event.which == 27) this.setMarkerModeEnabled(false);
    // Draw marker on enter
    var lat = $(".lat-picker").val();
    var lon = $(".lon-picker").val();
    if (event.which == 13 && lat != "" && lon != ""){
      this.setMarker(lat,lon);
    }
  }).bind(this));

  // Respond when the user cancels marker drawing.
  $(".point .cancel").click(this.setMarkerModeEnabled.bind(this, false));

  // Respond when the user clears the marker.
  $(".point .clear").click(this.clearMarker.bind(this));
};

/**
 * Returns the coordinates of the current marker.
 * @return [<lon>, <lat>] coordinates of the current marker as long or null
 */
ntst.App.prototype.getMarkerCoordinates = function() {

  if(this.currentMarker){
    var lat = this.currentMarker.getPosition().lat();
    var lon = this.currentMarker.getPosition().lng();

    return [lon, lat];
  }else{
    return null;
  }
};

/**
 * Sets whether drawing on the map is enabled.
 * @param {boolean} enabled Whether drawing mode is enabled.
 */
ntst.App.prototype.setMarkerModeEnabled = function(enabled) {
  

  if(enabled){
    var lat = $(".lat-picker").val();
    var lon = $(".lon-picker").val();
    if(lat != "" && lon != ""){
      this.setMarker(lat,lon);
    }else{
      $(".point").toggleClass("drawing", enabled);
      this.map.setOptions({draggableCursor:"crosshair"});
      this.markerListener = google.maps.event.addListener(this.map, "click", (function(event) {
        this.setMarker(event.latLng.lat(),event.latLng.lng());
      }).bind(this));
      this.coordinatesUpdater = google.maps.event.addListener(this.map, "mousemove", (function(event) {
        // update the gui (show coordinates)
        $(".lat-picker").val(event.latLng.lat());
        $(".lon-picker").val(event.latLng.lng());
      }).bind(this));
    }
  }else{
    if(this.markerListener){
      google.maps.event.removeListener(this.markerListener);
    }
    if(this.coordinatesUpdater){
      google.maps.event.removeListener(this.coordinatesUpdater);
    }
    this.map.setOptions({draggableCursor:""});
  }
};

/**
* Set the current marker on the specific coordinates.
*/
ntst.App.prototype.setMarker = function(lat,lon) {
  var lat_f = parseFloat(lat);
  var lon_f = parseFloat(lon);
  if(isNaN(lat_f) || isNaN(lon_f)){
    this.setAlert("coordinates", "danger", "Failed to parse Lat or Lon!", "Please enter valid float numbers.");
    return
  }
  var latLng = new google.maps.LatLng(lat_f,lon_f);

  if(this.currentMarker){
    this.currentMarker.setPosition(latLng);
  }else{
    this.currentMarker = new google.maps.Marker({
      position: latLng,
      map: this.map
    });
    this.currentMarker.addListener("click",(function(){
      this.clearMarker();
      this.setMarkerModeEnabled(true);
    }).bind(this));
  }

  // update the gui (activate buttons, show coordinates, disable tooltips)
  $(".point").addClass("selected");
  $(".chart, .compute").attr("disabled", false);
  //$("#message-selected-text").html("Set to -> lat: " + latLng.lat() + ", lon: " + latLng.lng() + " (press enter to update)");
  $("#chart-tooltip, #compute-tooltip").tooltip("disable");
  $(".point").toggleClass("drawing", false);

  this.setMarkerModeEnabled(false);("");
};

/**
* Clears the current marker from the map and enables drawing.
*/
ntst.App.prototype.clearMarker = function() {
  if(this.currentMarker){
    this.currentMarker.setMap(null);
  }
  this.currentMarker = null;

  // update the gui
  $(".point").removeClass("selected");
  $(".chart").attr("disabled", true);
  $("#chart-tooltip").tooltip("enable");
  if($(".export").attr("disabled")){
    $(".compute").attr("disabled",true);
    $("#compute-tooltip").tooltip("enable");
  }
  $(".lat-picker").val("");
  $(".lon-picker").val("");
};


///////////////////////////////////////////////////////////////////////////////
//                           Region selection.                               //
///////////////////////////////////////////////////////////////////////////////

/**
* Initializes the region picker.
*/
ntst.App.prototype.initRegionPicker = function() {
  // Respond when the user chooses to draw a polygon.
  $(".region .draw").click(this.setDrawingModeEnabled.bind(this, true));

  // Respond when the user draws a polygon on the map.
  google.maps.event.addListener(
      this.drawingManager, "overlaycomplete",
      (function(event) {
        if (this.getDrawingModeEnabled()) {
          this.handleNewPolygon(event.overlay);
        } else {
          event.overlay.setMap(null);
        }
      }).bind(this));

  // Cancel drawing mode if the user presses escape.
  $(document).keydown((function(event) {
    if (event.which == 27) this.setDrawingModeEnabled(false);
  }).bind(this));

  // Respond when the user cancels polygon drawing.
  $(".region .cancel").click(this.setDrawingModeEnabled.bind(this, false));

  // Respond when the user clears the polygon.
  $(".region .clear").click(this.clearPolygon.bind(this));
};

/**
 * Returns the coordinates of the currently drawn polygon.
 * @return {Array<Array<number>>} A list of coordinates describing
 *    the currently drawn polygon (or null if no polygon is drawn).
 */
ntst.App.prototype.getPolygonCoordinates = function() {
  if(this.currentPolygon){
    var points = this.currentPolygon.getPath().getArray();
    var twoDimensionalArray = points.map(function(point) {
      return [point.lng(), point.lat()];
    });
    return twoDimensionalArray;
  }else{
    return null;
  }
};

/**
 * Sets whether drawing on the map is enabled.
 * @param {boolean} enabled Whether drawing mode is enabled.
 */
ntst.App.prototype.setDrawingModeEnabled = function(enabled) {
  $(".region").toggleClass("drawing", enabled);
  var mode = enabled ? google.maps.drawing.OverlayType.POLYGON : null;
  this.drawingManager.setOptions({drawingMode: mode});
};

/**
 * Sets whether drawing on the map is enabled.
 * @return {boolean} Whether drawing mode is enabled.
 */
ntst.App.prototype.getDrawingModeEnabled = function() {
  return $(".region").hasClass("drawing");
};

/**
* Clears the current polygon from the map and enables drawing.
*/
ntst.App.prototype.clearPolygon = function() {
  this.currentPolygon.setMap(null);
  this.currentPolygon = null;

  // updates the gui
  $(".region").removeClass("selected");
  $(".export").attr("disabled", true);
  $("#export-tooltip").tooltip("enable");
  if($(".chart").attr("disabled")){
    $(".compute").attr("disabled",true);
    $("#compute-tooltip").tooltip("enable");
  }
};

/**
 * Stores the current polygon drawn on the map and disables drawing.
 * @param {Object} opt_overlay The new polygon drawn on the map. If
 *     undefined, the default polygon is treated as the new polygon.
 */
ntst.App.prototype.handleNewPolygon = function(opt_overlay) {
  this.currentPolygon = opt_overlay;
  $(".region").addClass("selected");
  $(".export, .compute").attr("disabled", false);
  $("#export-tooltip, #compute-tooltip").tooltip("disable");
  this.setDrawingModeEnabled(false);
};


///////////////////////////////////////////////////////////////////////////////
//                                   Alerts.                                 //
///////////////////////////////////////////////////////////////////////////////

/**
* Initializes alert functionality.
*/
ntst.App.prototype.initAlerts = function() {

  var socket = this.channel.open();

  socket.onmessage = (function(message){
    var data = JSON.parse(message.data);
    this.setAlert(data.id,data.style,data.line1,data.line2);
  }).bind(this);
};

/**
 * Sets the alert with the given name to the have the class and content given.
 * The alert is created if it doesn't already exist.
 * @param {string} name The name of the alert to set.
 * @param {string} cls The type of the alert for Bootstrap CSS styling.
 * @param {string} line1 The first line of the alert text.
 * @param {string=} opt_line2 The second line of the alert text.
 */
ntst.App.prototype.setAlert = function(name, cls, line1, opt_line2) {
  var alert;
  var existing = this.findAlert(name);
  if (existing) {
    // Replace the contents of the existing alert, if any.
    $(".alert[data-alert-name='" + name + "'] p").remove();
    alert = existing.removeClass()
        .addClass("alert alert-dismissable alert-" + cls);
  } else {
    // Create a new alert if needed.
    alert = $(".templates .alert").clone()
        .addClass("alert-" + cls)
        .attr("data-alert-name", name);
    $(".alerts").append(alert);
  }
  alert.append($("<p/>").append(line1))
       .append($("<p/>", {class: "line2"}).append(opt_line2))
       .addClass("visible");
};

/**
 * Removes the alert with the given name.
 * @param {string} name The name of the alert to remove.
 */
ntst.App.prototype.removeAlert = function(name) {
  var cur = this.findAlert(name);
  if (cur) {
    cur.removeClass("visible");
    // Remove the alert once its animation finishes.
    cur.on("transitionend", function() {
      if (!cur.hasClass("visible")) {
        cur.remove();
      }
    });
  }
};

/**
 * Finds the alert with the given name, if any.
 * @param {string} name The name of the alert to find.
 * @return {Object} The jQuery DOM wrapper for the alert with the given name.
 */
ntst.App.prototype.findAlert = function(name) {
  var existing = $(".alert[data-alert-name='" + name + "']");
  return existing.length ? existing : undefined;
};


///////////////////////////////////////////////////////////////////////////////
//                             Layer management.                             //
///////////////////////////////////////////////////////////////////////////////

/**
* Updates the image based on the current control panel config.
*/
ntst.App.prototype.refreshImage = function() {
  var name = "ndvi";
  var options = this.getOptions();
  var optionsString = this.getOptionsString();
  if (this.layerPaths[name] == optionsString){
    return;  // If the map hasn't changed since the last update, exit early.
  } else {

    // remove layer and hide the bandSwitcher
    this.removeLayer(name);
    $("#bandSwitcher").css("display","none");
    $("#bandSwitcher").html("");

    // Encode the parameters in the URL.
    this.setLayer(name, options, optionsString, false);
  }
};

/**
 * Sets the layer with the given name to the map. The
 * layer is created if it doesn't already exist.
 * @param {string} name The name of the layer to set.
 * @param {string} options options for the map send to the URL /mapid. Should return mapid and token
 * @param {string} optionsString String of all option values connected
 */
ntst.App.prototype.setLayer = function(name, options, optionsString) {
  this.removeLayer(name);
  var showLoadingFn = this.setAlert.bind(this, name, "warning", "Map is loading.");

  var onError = (function(error) {
    delete this.layerPaths[name];
    this.setAlert(name, "danger", "Map failed to load.", error);
  }).bind(this);

  var onDone = (function(data) {

    //remeber first band, to make it visible later
    var firstBand = "";
    this.layerBands = {};
    for (var i = 0; i < data["bands"].length; i++) {
      var band = data["bands"][i];
      if(i==0){
        firstBand = band.name; //remeber first band, to make it visible later
      }

      this.layerBands[band.name] = new ee.MapLayerOverlay(ntst.App.EE_URL + "/map", band.mapid, band.token, {name: name});

      //add overlay invisible to map
      this.layerBands[band.name].setOpacity(0);
      this.map.overlayMapTypes.push(this.layerBands[band.name]);

      //add the band name to the MapLayerOverlay to access it in the callback
      this.layerBands[band.name].band_name = band.name;
      // Hide and show the 'layer loading' alert as needed.
      this.layerBands[band.name].addTileCallback((function(event) {
        showLoadingFn(event.target.band_name + ": " + event.count + " tiles remaining.");
        if (event.count === 0) {
          this.removeAlert(name);
        }
      }).bind(this));

      // create the band switcher
      var div = document.createElement("div");
      $(div).attr("class","checkbox checkbox");

      var input = document.createElement("input");
      $(input).attr("type","radio");
      $(input).attr("id",band.name);
      $(input).attr("name","bands");

      // if band is checked make it visible and hide all other
      $(input).change(band.name,(function(event){
        if(event.target.checked){
          for(var key in this.layerBands){
            if(key == event.data){
              this.layerBands[key].setOpacity(1);
              $("#blank").prop("checked",false);
            }else{
              this.layerBands[key].setOpacity(0);
            }
          }
        }
      }).bind(this));

      var label = document.createElement("label");
      $(label).attr("for",band.name);
      $(label).html(band.name);

      $("#bandSwitcher").append($(div).append(input).append(label));
    };


    //add blank option
    var div = document.createElement("div");
    $(div).attr("class","checkbox checkbox-warning");

    var input = document.createElement("input");
    $(input).attr("type","checkbox");
    $(input).attr("id","blank");
    $(input).css("margin-left","0px");
    $(input).change((function(event){
      if(event.target.checked){
        for(var key in this.layerBands){
          this.layerBands[key].setOpacity(0);
        }
      }else{
        for(var key in this.layerBands){
          if($("#" + key).prop("checked")){
            this.layerBands[key].setOpacity(1);
          }else{
            this.layerBands[key].setOpacity(0);
          }
        }
      }
    }).bind(this));

    var label = document.createElement("label");
    $(label).attr("for","blank");
    $(label).html("hide all");

    $("#bandSwitcher").prepend($(div).append(input).append(label));

    //display the band switcher
    $("#bandSwitcher").css("display","inline");

    //make the first band visible
    this.layerBands[firstBand].setOpacity(1);
    $("#" + firstBand).prop("checked",true);

  }).bind(this);

  showLoadingFn();
  this.layerPaths[name] = optionsString;
  this.layerRequests[name] = ntst.App.handleRequest($.post("/mapid",options), onDone, onError);
};

/**
 * Removes the map layer(s) with the given name.
 * @param {string} name The name of the layer(s) to remove.
 */
ntst.App.prototype.removeLayer = function(name) {
  // Cancel any outstanding requests to avoid calling the callback (which
  // would add the now-obsolete layer to the map).
  if (this.layerRequests[name]) {
    this.layerRequests[name].abort();
    delete this.layerRequests[name];
  }
  // Delete the current path.
  delete this.layerPaths[name];
  this.removeAlert(name);
  this.map.overlayMapTypes.forEach((function(mapType, index) {
    if (mapType && mapType.name == name) {
      this.map.overlayMapTypes.removeAt(index);
    }
  }).bind(this));
};


///////////////////////////////////////////////////////////////////////////////
//                                Exporting.                                 //
///////////////////////////////////////////////////////////////////////////////

/**
 * Exports the currently configured image to Drive.
 * When the exported image is ready, the download link will be shared with the user.
 */
ntst.App.prototype.exportImage = function() {
  var params = this.getOptions();
  ntst.App.handleRequest($.post("/export", params), null, this.setAlert.bind(this, "export-" + params.filename, "danger", "Export failed."));
};

/**
* Creates a download link for the currently configured image.
* The download link creates a zip file with a tif image for each band,
* computing happens on the fly so that the dowload is not very stable.
* Also it is limited by a max file size of 1024 MB.
*/
ntst.App.prototype.getDownloadUrl = function(){
  var params = this.getOptions();
  ntst.App.handleRequest($.post("/download", params), null, this.setAlert.bind(this, "download-" + params.filename, "danger", "Download creation failed."));
}

/**
* Creates a chart of the data at the selected point.
* Also provides a temporary full screen chart url where the chart can be saved as image or as table.
*/
ntst.App.prototype.getChart = function(){
  var params = this.getOptions();
  ntst.App.handleRequest($.post("/chart", params), null, this.setAlert.bind(this, "chart-" + params.filename, "danger", "Chart creation failed."));
}


///////////////////////////////////////////////////////////////////////////////
//                        Static helpers                                     //
///////////////////////////////////////////////////////////////////////////////

/**
 * Creates a Google Map with the given map type rendered.
 * The map is anchored to the DOM element with the CSS class 'map'.
 * @param {Element} el The element to render the map into.
 * @return {google.maps.Map} A map instance with the map type rendered.
 */
ntst.App.createMap = function(el) {
  var mapOptions = {
    center: ntst.App.DEFAULT_CENTER,
    zoom: ntst.App.DEFAULT_ZOOM,
    streetViewControl: false,
    scaleControl: true
  };
  var mapEl = $(".map").get(0);
  var map = new google.maps.Map(el, mapOptions);
  return map;
};

/**
 * Creates a drawing manager for the passed-in map.
 * @param {google.maps.Map} map The map for which to create a drawing
 *     manager.
 * @return {google.maps.drawing.DrawingManager} A drawing manager for
 *     the given map.
 */
ntst.App.createDrawingManager = function(map) {
  var drawingManager = new google.maps.drawing.DrawingManager({
    drawingControl: false,
    polygonOptions: {
      fillColor: "#ff0000",
      strokeColor: "#ff0000"
    }
  });
  drawingManager.setMap(map);
  return drawingManager;
};

/**
 * Handles the success or failure of the data request.
 * @param {Object} request The jqXHR sent.
 * @param {function(Object)} onDone The function to call if the request
 *     succeeds, with the data object as an argument.
 * @param {function(string)} onError The function to call if the request
 *     fails, with an error message as an argument.
 * @return {Object} The original request, against which further callbacks
 *     can be registered.
 */
ntst.App.handleRequest = function(request, onDone, onError) {
  request.done(function(data) {
    if (data && data.error) {
      onError(data.error);
    } else {
      if (onDone) onDone(data);
    }
  }).fail(function(jqXHR, textStatus) {
    onError("HTTP Status: " + jqXHR.status);
  });
  return request;
};
