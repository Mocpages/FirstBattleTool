(function () {
  'use strict';

  var FLAT_TO_FLAT_M = 1000;
  var R_M = FLAT_TO_FLAT_M / Math.sqrt(3);
  var CLICK_DELAY_MS = 280;

  var SYMBOL_SIZE = 44;
  var BATTALION_SYMBOL_SIZE = 52;
  /** Company marker: combat totals column (px), equipment column (px). */
  var COMBAT_SIDEBAR_W = 44;
  var EQUIP_SIDEBAR_W = 68;
  /** Hex mesh only at this zoom level and closer (zoomed further in). */
  var HEX_GRID_MIN_ZOOM = 11;
  /** If closest pair of companies in the same battalion is under this distance in px, show battalion marker only. */
  var COMPANY_OVERLAP_PIXELS = 48;
  /** Blue LOS outline is the outer perimeter of ZOC ∪ any hex centre within this of a ZOC hex (≈ ZoC boundary + 3000 m). */
  var LOS_BUFFER_PAST_ZOC_M = 3000;

  /** Play area (lat/lon). Hex centres outside this rectangle are hidden. */
  var PLAY_BOUNDS = { north: 50.919, south: 49.92, west: 8.145, east: 11.584 };

  /** Hex-grid origin — adjust when your raster / theatre uses a different reference. */
  var hexOriginLat = 50.55;
  var hexOriginLon = 9.675;

  /** @typedef {'moving' | 'halted'} UnitActivity */
  /** @typedef {'tracked' | 'truck'} VehicleType */

  /**
   * @typedef {{
   *   company: string,
   *   battalion: string,
   *   lat: number,
   *   lon: number,
   *   side: string,
   *   activity: UnitActivity,
   *   vehicle: VehicleType,
   *   destinationKey: string|null,
   *   routeWaypointKeys: string[],
   *   routePath: string[]|null,
   *   routeLegIndex: number,
   *   accumMovePoints: number,
   *   spotted: boolean,
   *   equipmentSpecs: { name: string, count: number }[],
   *   totalDirectFire: number,
   *   totalIndirectFire: number,
   *   totalCloseCombat: number
   * }} Unit
   */

  /**
   * Loaded from mv_cost.tif (single-band). Geographic TIFFs use bbox-based pixel lookup (north-up).
   * @type {{
   *   width: number,
   *   height: number,
   *   geoExtents: {west: number, south: number, east: number, north: number}|null,
   *   originLon: number,
   *   originLat: number,
   *   pixelW: number,
   *   pixelH: number,
   *   nodata: number|null,
   *   data: Uint8Array|Int8Array|Uint16Array|Int16Array|Uint32Array|Int32Array|Float32Array|Float64Array,
   * }|null}
   */
  var mvCostRaster = null;
  var terrainOverlayEnabled = false;

  /** @type {Unit[]} */
  var units = [];
  /** @type {Object.<string, { movement: number, dfRange: number, dfScore: number, ifRange: number, ifScore: number, ccScore: number }>} */
  var unitStatsByName = {};
  /** @type {Object.<string, L.Marker>} */
  var markerByKey = {};
  /**
   * @typedef {{ battalionKey: string, battalion: string, side: string, units: Unit[] }} BattalionGroup
   */
  /** @type {Object.<string, BattalionGroup>} */
  var battalionGroupsMap = {};
  /** Battalion centroid markers (only used when aggregated). */
  /** @type {Object.<string, L.Marker>} */
  var battalionMarkerByKey = {};
  /** Last refresh: battalionKey → battalion centroid mode active */
  /** @type {Object.<string, boolean>} */
  var battalionOverlapAgg = {};

  /** Keys "targetUnitKey|hexKey|spotterUnitKey" — user already confirmed/denied acquisition for this hex visit. */
  var acquisitionSilenced = {};
  /** Halted enemy within this many hexes (grid distance) of mover's hex → proximity acquisition. */
  var PROXIMITY_ACQUISITION_HEX_DISTANCE = 3;
  /** @type {{ spotter: Unit, target: Unit, enteredHexKey: string, fromHexKey: string, spotKind?: string }[]} */
  var acquisitionQueue = [];
  var acquisitionFlowActive = false;

  /** From /api/bootstrap */
  var mvCostMeta = { loaded: false };
  var selectionOverlayCache = null;

  function getUnitByKey(key) {
    var i;
    for (i = 0; i < units.length; i++) {
      if (units[i].key === key || unitKey(units[i].side, units[i]) === key) {
        return units[i];
      }
    }
    return null;
  }

  function mergeServerUnit(su) {
    var u = getUnitByKey(su.key);
    if (!u) return null;
    u.key = su.key;
    u.lat = su.lat;
    u.lon = su.lon;
    u.activity = su.activity;
    u.destinationKey = su.destinationKey;
    u.routeWaypointKeys = su.routeWaypointKeys || [];
    u.routePath = su.routePath;
    u.routeLegIndex = su.routeLegIndex;
    u.accumMovePoints = su.accumMovePoints;
    u.spotted = su.spotted;
    u.equipmentSpecs = su.equipmentSpecs || u.equipmentSpecs;
    u.totalDirectFire = su.totalDirectFire;
    u.totalIndirectFire = su.totalIndirectFire;
    u.totalCloseCombat = su.totalCloseCombat;
    return u;
  }

  function applyServerUnits(serverUnits) {
    if (!serverUnits) return;
    var i;
    for (i = 0; i < serverUnits.length; i++) {
      mergeServerUnit(serverUnits[i]);
    }
  }

  function updateTimebarFromApi(tb, simMs) {
    if (simMs != null) simInstantMs = simMs;
    var elMain = document.getElementById('timebar-main');
    var elDn = document.getElementById('timebar-daynight');
    var elSu = document.getElementById('timebar-sun');
    if (tb) {
      if (elMain) elMain.textContent = tb.main;
      if (elDn) elDn.textContent = tb.daynight;
      if (elSu) elSu.textContent = tb.sunLine;
    } else {
      updateTimebarDisplay();
    }
  }

  function pushAcquisitionEventsFromApi(events) {
    if (!events || !events.length) return;
    var i;
    for (i = 0; i < events.length; i++) {
      var ev = events[i];
      var spotter = getUnitByKey(ev.spotter_key);
      var target = getUnitByKey(ev.target_key);
      if (!spotter || !target) continue;
      acquisitionQueue.push({
        spotter: spotter,
        target: target,
        enteredHexKey: ev.entered_hex_key,
        fromHexKey: ev.from_hex_key,
        spotKind: ev.spot_kind,
      });
    }
  }

  function syncMvCostMetaFromBootstrap(mv) {
    mvCostMeta = mv || { loaded: false };
    if (mv && mv.loaded && mv.west != null) {
      syncMvCostBoundsOverlay({
        west: mv.west,
        south: mv.south,
        east: mv.east,
        north: mv.north,
      });
    } else {
      syncMvCostBoundsOverlay(null);
    }
  }

  /** Axial directions (q, r), pointy-top hex grid — redblobgames.com/grids/hexagons */
  var AXIAL_NEIGHBORS = [
    [1, 0],
    [1, -1],
    [0, -1],
    [-1, 0],
    [-1, 1],
    [0, 1],
  ];

  var opentopomap = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    maxZoom: 17,
    attribution:
      'Map: <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
    subdomains: 'abc',
  });

  var map = L.map('map', {
    zoomControl: true,
    preferCanvas: true,
  }).setView([50.55, 9.675], 11);

  opentopomap.addTo(map);

  /** @type {L.Rectangle} */
  var playBoundsOverlay = L.rectangle(
    L.latLngBounds(
      [PLAY_BOUNDS.south, PLAY_BOUNDS.west],
      [PLAY_BOUNDS.north, PLAY_BOUNDS.east],
    ),
    {
      color: '#b45309',
      weight: 2,
      opacity: 0.95,
      fill: false,
      dashArray: '10 8',
      interactive: false,
    },
  );
  playBoundsOverlay.addTo(map);

  /** Last failure reason when mv_cost.tif fails (shown in status + console). */
  var mvCostLoadFailureReason = '';

  /** Raster extent overlay (shown when mv_cost.tif loads successfully). */
  /** @type {L.Rectangle|null} */
  var mvCostBoundsOverlay = null;

  function syncMvCostBoundsOverlay(ge) {
    if (mvCostBoundsOverlay) {
      map.removeLayer(mvCostBoundsOverlay);
      mvCostBoundsOverlay = null;
    }
    if (!ge || !(ge.east > ge.west) || !(ge.north > ge.south)) {
      return;
    }
    mvCostBoundsOverlay = L.rectangle(L.latLngBounds([ge.south, ge.west], [ge.north, ge.east]), {
      color: '#7c3aed',
      weight: 2,
      opacity: 0.92,
      fill: false,
      dashArray: '8 6',
      interactive: false,
    }).addTo(map);
  }

  map.createPane('hexPane');
  map.getPane('hexPane').style.zIndex = '390';
  map.createPane('zocPane');
  map.getPane('zocPane').style.zIndex = '395';
  map.createPane('losPane');
  map.getPane('losPane').style.zIndex = '396';
  map.createPane('routePane');
  map.getPane('routePane').style.zIndex = '398';
  map.createPane('terrainOverlayPane');
  map.getPane('terrainOverlayPane').style.zIndex = '391';
  /** preferCanvas would otherwise skip SVG; keep LOS dashed lines visible on losPane */
  var losSvgRenderer = L.svg({ pane: 'losPane', padding: 1 });

  /** Exercise clock (Fulda); civil display = UTC + 2 h. */
  var FULDA_SUN_LAT = 50.55;
  var FULDA_SUN_LON = 9.675;
  var FULDA_OFFSET_MS = 2 * 60 * 60 * 1000;
  var SIM_TICK_MS = 5 * 60 * 1000;
  /** 1989-09-19 08:00 Fulda (+2) → UTC 06:00 */
  var simInstantMs = Date.UTC(1989, 8, 19, 6, 0, 0);
  var MONTH_ABBR = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

  function pad2Clock(n) {
    return (n < 10 ? '0' : '') + n;
  }

  function fuldaWallParts(simMs) {
    var wall = simMs + FULDA_OFFSET_MS;
    var d = new Date(wall);
    return {
      y: d.getUTCFullYear(),
      mo: d.getUTCMonth() + 1,
      day: d.getUTCDate(),
      hh: d.getUTCHours(),
      mm: d.getUTCMinutes(),
    };
  }

  function formatExerciseMainLine(simMs) {
    var p = fuldaWallParts(simMs);
    var yy = p.y % 100;
    return (
      pad2Clock(p.hh) +
      pad2Clock(p.mm) +
      ' ' +
      p.day +
      MONTH_ABBR[p.mo - 1] +
      pad2Clock(yy)
    );
  }

  function wallHmFromUtcInstant(utcMs) {
    var wall = utcMs + FULDA_OFFSET_MS;
    var d = new Date(wall);
    return pad2Clock(d.getUTCHours()) + ':' + pad2Clock(d.getUTCMinutes());
  }

  function addWallCalendarDays(y, mo, day, deltaDays) {
    var ref = Date.UTC(y, mo - 1, day, 12, 0, 0) + deltaDays * 86400000;
    var t = new Date(ref);
    return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() };
  }

  function getSunCalcApi() {
    return typeof SunCalc !== 'undefined' ? SunCalc : typeof window !== 'undefined' ? window.SunCalc : null;
  }

  function solarTimesFuldaWallDay(y, mo, day) {
    var sc = getSunCalcApi();
    if (!sc || typeof sc.getTimes !== 'function') return null;
    var noonRefUtc = Date.UTC(y, mo - 1, day, 10, 0, 0);
    return sc.getTimes(new Date(noonRefUtc), FULDA_SUN_LAT, FULDA_SUN_LON);
  }

  function formatDurationHM(msDur) {
    if (!(msDur > 0)) return '00:00';
    var totalMin = Math.floor(msDur / 60000);
    var hh = Math.floor(totalMin / 60);
    var mm = totalMin % 60;
    return pad2Clock(hh) + ':' + pad2Clock(mm);
  }

  function timebarComputedStrings(simMs) {
    var main = formatExerciseMainLine(simMs);
    var p = fuldaWallParts(simMs);
    var st = solarTimesFuldaWallDay(p.y, p.mo, p.day);

    if (!st) {
      return {
        main: main,
        daynight: '—',
        sunLine: 'Sunrise/sunset unavailable (SunCalc)',
      };
    }

    var riseMs = st.sunrise.getTime();
    var setMs = st.sunset.getTime();
    var sunriseClock = wallHmFromUtcInstant(riseMs);
    var sunsetClock = wallHmFromUtcInstant(setMs);

    var isDay = simMs >= riseMs && simMs < setMs;
    var daynightLabel = isDay ? 'Day' : 'Night';

    var countdownLabel;
    var countdownDur;
    if (simMs < riseMs) {
      countdownLabel = 'Sunrise in';
      countdownDur = riseMs - simMs;
    } else if (simMs < setMs) {
      countdownLabel = 'Sunset in';
      countdownDur = setMs - simMs;
    } else {
      var tom = addWallCalendarDays(p.y, p.mo, p.day, 1);
      var stNext = solarTimesFuldaWallDay(tom.y, tom.m, tom.d);
      countdownLabel = 'Sunrise in';
      countdownDur = stNext && stNext.sunrise ? stNext.sunrise.getTime() - simMs : 0;
    }

    var sunLine =
      'Sunrise ' +
      sunriseClock +
      ' · Sunset ' +
      sunsetClock +
      ' · ' +
      countdownLabel +
      ' ' +
      formatDurationHM(countdownDur);

    return {
      main: main,
      daynight: daynightLabel,
      sunLine: sunLine,
    };
  }

  function updateTimebarDisplay() {
    var elMain = document.getElementById('timebar-main');
    var elDn = document.getElementById('timebar-daynight');
    var elSu = document.getElementById('timebar-sun');
    var s = timebarComputedStrings(simInstantMs);
    if (elMain) elMain.textContent = s.main;
    if (elDn) elDn.textContent = s.daynight;
    if (elSu) elSu.textContent = s.sunLine;
  }

  (function initExerciseClockUI() {
    var playBtn = document.getElementById('timebar-play');
    if (playBtn) {
      playBtn.addEventListener('click', function (e) {
        if (e && e.preventDefault) e.preventDefault();
        if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
        if (acquisitionFlowActive) return;
        GameApi.simTick()
          .then(function (data) {
            applyServerUnits(data.units);
            updateTimebarFromApi(data.timebar, data.simInstantMs);
            pushAcquisitionEventsFromApi(data.newAcquisitionEvents);
            syncBattalionAggregateMarkers();
            var uu;
            for (uu = 0; uu < units.length; uu++) {
              refreshUnitMarkerIcon(units[uu]);
            }
            refreshMarkerDisplay();
            redrawHexGrid();
            updateMarkerClasses();
            beginAcquisitionFlowIfQueued();
          })
          .catch(function (err) {
            console.error(err);
            setStatus('Sim tick failed: ' + err.message);
          });
      });
    }
    updateTimebarDisplay();
  })();

  function metersPerDegree(latDeg) {
    var latRad = (latDeg * Math.PI) / 180;
    return {
      lat: 111320,
      lon: 111320 * Math.cos(latRad),
    };
  }

  function hexVerticesMeters(cx, cy) {
    var pts = [];
    var k;
    for (k = 0; k < 6; k++) {
      var angle = Math.PI / 2 + (k * Math.PI) / 3;
      pts.push([cx + R_M * Math.cos(angle), cy + R_M * Math.sin(angle)]);
    }
    return pts;
  }

  function metersToLatLon(xy, originLat, originLon, scale) {
    var lat = originLat + xy[1] / scale.lat;
    var lon = originLon + xy[0] / scale.lon;
    return [lat, lon];
  }

  /** Axial (q,r) → meters (x,y east, north), pointy-top, R = circumradius */
  function axialToXY(q, r) {
    var x = R_M * Math.sqrt(3) * (q + r / 2);
    var y = R_M * (3 / 2) * r;
    return [x, y];
  }

  function cubeRound(x, y, z) {
    var rx = Math.round(x);
    var ry = Math.round(y);
    var rz = Math.round(z);
    var xDiff = Math.abs(rx - x);
    var yDiff = Math.abs(ry - y);
    var zDiff = Math.abs(rz - z);
    if (xDiff > yDiff && xDiff > zDiff) {
      rx = -ry - rz;
    } else if (yDiff > zDiff) {
      ry = -rx - rz;
    } else {
      rz = -rx - ry;
    }
    return [rx, ry, rz];
  }

  /** Nearest axial hex for a lat/lon using the global hex origin */
  function latLonToAxial(lat, lon) {
    var scale = metersPerDegree(hexOriginLat);
    var x = (lon - hexOriginLon) * scale.lon;
    var y = (lat - hexOriginLat) * scale.lat;
    var fq = ((Math.sqrt(3) / 3) * x - (1 / 3) * y) / R_M;
    var fr = ((2 / 3) * y) / R_M;
    var xC = fq;
    var zC = fr;
    var yC = -fq - fr;
    var cube = cubeRound(xC, yC, zC);
    return { q: cube[0], r: cube[2] };
  }

  function hexKey(q, r) {
    return q + ',' + r;
  }

  /** Axial hex grid distance (pointy-top), 0 = same cell. */
  function axialHexDistance(q1, r1, q2, r2) {
    return (Math.abs(q1 - q2) + Math.abs(q1 + r1 - q2 - r2) + Math.abs(r1 - r2)) / 2;
  }

  function hexKeyDistance(keyA, keyB) {
    var a = parseHexKey(keyA);
    var b = parseHexKey(keyB);
    return axialHexDistance(a[0], a[1], b[0], b[1]);
  }

  function parseHexKey(s) {
    var p = s.split(',');
    return [parseInt(p[0], 10), parseInt(p[1], 10)];
  }

  /** Hex centre [lat, lon] in WGS84 for axial (q,r). */
  function axialCenterLatLon(q, r) {
    var c = axialToXY(q, r);
    var scale = metersPerDegree(hexOriginLat);
    return metersToLatLon(c, hexOriginLat, hexOriginLon, scale);
  }

  function distanceMetersLatLon(lat1, lon1, lat2, lon2) {
    var sc = metersPerDegree((lat1 + lat2) / 2);
    var dy = (lat2 - lat1) * sc.lat;
    var dx = (lon2 - lon1) * sc.lon;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function distanceKmLatLon(lat1, lon1, lat2, lon2) {
    return distanceMetersLatLon(lat1, lon1, lat2, lon2) / 1000;
  }

  /**
   * All hex cells whose centres lie ≤ (ZoC hex circumradius + LOS_BUFFER_PAST_ZOC_M) from any ZoC occupied hex centre
   * (discrete analogue of buffering ZoC outward by LOS_BUFFER_PAST_ZOC_M).
   */
  function hexKeysLOSBufferBeyondZOC(zocKeysObj) {
    var keys = Object.keys(zocKeysObj);
    if (!keys.length) {
      return {};
    }

    var zocos = [];
    var zi;
    for (zi = 0; zi < keys.length; zi++) {
      var qr = parseHexKey(keys[zi]);
      var ll = axialCenterLatLon(qr[0], qr[1]);
      zocos.push({ q: qr[0], r: qr[1], lat: ll[0], lon: ll[1] });
    }

    var qmin = Infinity;
    var qmax = -Infinity;
    var rmin = Infinity;
    var rmax = -Infinity;
    zocos.forEach(function (zc) {
      qmin = Math.min(qmin, zc.q);
      qmax = Math.max(qmax, zc.q);
      rmin = Math.min(rmin, zc.r);
      rmax = Math.max(rmax, zc.r);
    });

    var stepMargin =
      Math.ceil((LOS_BUFFER_PAST_ZOC_M + R_M + FLAT_TO_FLAT_M / 2) / (R_M * 0.82)) + 3;
    var cutoff = LOS_BUFFER_PAST_ZOC_M + R_M;
    var expanded = {};
    var tq;
    var tr;
    for (tq = qmin - stepMargin; tq <= qmax + stepMargin; tq++) {
      for (tr = rmin - stepMargin; tr <= rmax + stepMargin; tr++) {
        var hub = axialCenterLatLon(tq, tr);
        var dnearest = Infinity;
        var zn;
        for (zn = 0; zn < zocos.length; zn++) {
          var d = distanceMetersLatLon(hub[0], hub[1], zocos[zn].lat, zocos[zn].lon);
          if (d < dnearest) dnearest = d;
        }
        if (dnearest <= cutoff + 1e-6) {
          expanded[hexKey(tq, tr)] = true;
        }
      }
    }
    return expanded;
  }

  /**
   * Discrete hex set matching the blue dashed LOS outline for a single unit: buffered expansion of that unit's ZoC
   * (same as redrawLineOfSight uses for merged selection, but for one unit's ZoC alone).
   */
  function hexKeysLineOfSightAreaForUnit(u) {
    return hexKeysLOSBufferBeyondZOC(hexesForUnitZOC(u));
  }

  /** Closed ring [v0..v5, v0] in lat/lon for hex (q,r) */
  function hexRingClosedLatLon(q, r) {
    var c = axialToXY(q, r);
    var scale = metersPerDegree(hexOriginLat);
    var verts = hexVerticesMeters(c[0], c[1]);
    var ring = verts.map(function (m) {
      return metersToLatLon(m, hexOriginLat, hexOriginLon, scale);
    });
    ring.push(ring[0]);
    return ring;
  }

  /** Halted: occupation hex plus six neighbors. Moving: occupation hex only. */
  function hexesForUnitZOC(u) {
    var axial = latLonToAxial(u.lat, u.lon);
    var q0 = axial.q;
    var r0 = axial.r;
    var set = {};
    set[hexKey(q0, r0)] = true;
    if (u.activity === 'moving') {
      return set;
    }
    var i;
    for (i = 0; i < AXIAL_NEIGHBORS.length; i++) {
      var dq = AXIAL_NEIGHBORS[i][0];
      var dr = AXIAL_NEIGHBORS[i][1];
      set[hexKey(q0 + dq, r0 + dr)] = true;
    }
    return set;
  }

  function summarizeBattalionActivity(us) {
    var moving = false;
    var halted = false;
    var ui;
    for (ui = 0; ui < us.length; ui++) {
      if (us[ui].activity === 'moving') moving = true;
      else halted = true;
      if (moving && halted) return 'mixed';
    }
    return moving ? 'moving' : 'halted';
  }

  function mergeHexKeySets(a, b) {
    var k;
    var out = {};
    for (k in a) {
      if (Object.prototype.hasOwnProperty.call(a, k)) out[k] = true;
    }
    for (k in b) {
      if (Object.prototype.hasOwnProperty.call(b, k)) out[k] = true;
    }
    return out;
  }

  function roundCoord(c) {
    return Math.round(c * 1e5) / 1e5;
  }

  function edgeKey(pA, pB) {
    var a = roundCoord(pA[0]) + ',' + roundCoord(pA[1]);
    var b = roundCoord(pB[0]) + ',' + roundCoord(pB[1]);
    return a < b ? a + '|' + b : b + '|' + a;
  }

  /** Perimeter edges: shared edges appear twice when both hexes are in the set */
  function buildPerimeterPolylines(hexKeys) {
    var keys = Object.keys(hexKeys);
    if (!keys.length) return [];

    var edgeCount = {};
    var edgeSeg = {};
    var ki;
    for (ki = 0; ki < keys.length; ki++) {
      var qr = parseHexKey(keys[ki]);
      var ring = hexRingClosedLatLon(qr[0], qr[1]);
      var ei;
      for (ei = 0; ei < 6; ei++) {
        var p0 = ring[ei];
        var p1 = ring[ei + 1];
        var ek = edgeKey(p0, p1);
        edgeCount[ek] = (edgeCount[ek] || 0) + 1;
        edgeSeg[ek] = [p0, p1];
      }
    }

    var lines = [];
    var ek2;
    for (ek2 in edgeCount) {
      if (Object.prototype.hasOwnProperty.call(edgeCount, ek2) && edgeCount[ek2] === 1) {
        lines.push(edgeSeg[ek2]);
      }
    }
    return lines;
  }

  function hexCenterInPlay(lat, lon) {
    return (
      PLAY_BOUNDS.south <= lat &&
      lat <= PLAY_BOUNDS.north &&
      PLAY_BOUNDS.west <= lon &&
      lon <= PLAY_BOUNDS.east
    );
  }

  /** Reject projected extent; permit large regional / continental geographic rasters. */
  function bboxLooksLikeGeographicDegrees(bb) {
    var west = bb[0];
    var south = bb[1];
    var east = bb[2];
    var north = bb[3];
    if (![west, south, east, north].every(isFinite)) {
      return false;
    }
    if (!(east > west && north > south)) {
      return false;
    }
    if (south < -90.5 || north > 90.5) {
      return false;
    }
    if (!(west >= -720 && east <= 720)) {
      return false;
    }
    var lonSpan = east - west;
    var latSpan = north - south;
    if (lonSpan > 361 || latSpan > 181) {
      return false;
    }
    return true;
  }

  function isMvNodata(raw, nodata) {
    if (raw == null || raw === '' || !isFinite(Number(raw))) {
      return true;
    }
    if (nodata == null || !isFinite(nodata)) {
      return false;
    }
    var v = Number(raw);
    var n = Number(nodata);
    if (v === n) return true;
    var tol = 1e-5 * (1 + Math.abs(n));
    return Math.abs(v - n) <= tol;
  }

  /** @returns {[number, number]|null} pixel column/row inclusive 0 … w–1/h–1 */
  function mvRasterPixelIxIy(lat, lon) {
    var R = mvCostRaster;
    if (!R || !isFinite(lat) || !isFinite(lon)) {
      return null;
    }
    var ix;
    var iy;
    var G = R.geoExtents;
    var lonSpan;
    var latSpan;
    if (G && G.east > G.west && G.north > G.south) {
      lonSpan = G.east - G.west;
      latSpan = G.north - G.south;
      if (lon < G.west || lon > G.east || lat < G.south || lat > G.north) {
        return null;
      }
      /** North-up EPSG-style layout: iy = 0 is northern latitude (max). */
      var fxPix = ((lon - G.west) / lonSpan) * R.width;
      var fyPix = ((G.north - lat) / latSpan) * R.height;
      ix = Math.min(R.width - 1, Math.max(0, Math.floor(fxPix)));
      iy = Math.min(R.height - 1, Math.max(0, Math.floor(fyPix)));
    } else {
      lonSpan = R.pixelW;
      latSpan = R.pixelH;
      if (!(Math.abs(lonSpan) >= 1e-30 && Math.abs(latSpan) >= 1e-30)) {
        return null;
      }
      var fxAffine = (lon - R.originLon) / lonSpan;
      var fyAffine = (lat - R.originLat) / latSpan;
      ix = Math.floor(fxAffine);
      iy = Math.floor(fyAffine);
      if (ix < 0 || iy < 0 || ix >= R.width || iy >= R.height) {
        return null;
      }
    }
    return [ix, iy];
  }

  /** @returns {number|null} sampled cell value before rounding; null = outside / nodata */
  function rawMvCostAtLatLon(lat, lon) {
    var R = mvCostRaster;
    if (!R || !R.data) {
      return null;
    }
    var pr = mvRasterPixelIxIy(lat, lon);
    if (!pr) {
      return null;
    }
    var idx = pr[1] * R.width + pr[0];
    /** @type {number} */
    var v = R.data[idx];
    if (isMvNodata(v, R.nodata)) {
      return null;
    }
    return v/14;
  }

  /**
   * Rounded MP cost; sub-unit positives (e.g. 0.4) snap to ≥ 1 MP. Zero/nodata rejects.
   */
  function mvCostRoundedAtLatLon(lat, lon) {
    var raw = rawMvCostAtLatLon(lat, lon);
    if (raw == null || !isFinite(Number(raw))) {
      return null;
    }
    var r = Number(raw);
    if (r < 0) {
      return null;
    }
    var n = Math.round(r);
    if (n >= 1) {
      return n;
    }
    if (r > 1e-6) {
      return 1;
    }
    return null;
  }

  function mvCostRoundedAtHexKey(hexKey) {
    var qr = parseHexKey(hexKey);
    if (qr.some(isNaN)) return null;
    var ll = axialCenterLatLon(qr[0], qr[1]);
    return mvCostRoundedAtLatLon(ll[0], ll[1]);
  }

  function hexHasMvRaster(hexKey) {
    return mvCostRoundedAtHexKey(hexKey) != null;
  }

  /**
   * Loads mv_cost.tif (geographic CRS). Calls redraw after success or failure when map layers exist.
   * @returns {Promise<boolean>}
   */
  function loadMvCostTifMaybe() {
    /** @type {typeof GeoTIFF|undefined} */
    var GT =
      typeof globalThis !== 'undefined' &&
      typeof globalThis.GeoTIFF !== 'undefined'
        ? globalThis.GeoTIFF
        : typeof window !== 'undefined' &&
            typeof /** @type {any} */ (window).GeoTIFF !== 'undefined'
          ? /** @type {any} */ (window).GeoTIFF
          : undefined;
    if (!GT || typeof GT.fromUrl !== 'function') {
      mvCostRaster = null;
      mvCostLoadFailureReason = 'GeoTIFF library missing (dist-browser/geotiff.min.js)';
      syncMvCostBoundsOverlay(null);
      return Promise.resolve(false);
    }

    mvCostLoadFailureReason = '';

    var fromBuffer = typeof GT.fromArrayBuffer === 'function' ? GT.fromArrayBuffer.bind(GT) : null;

    /** Full-file fetch avoids Range/stream issues with Python SimpleHTTPRequestHandler + fromUrl COG reads. */
    function openMvCostGeoTiff() {
      var url = 'mv_cost.tif';
      if (fromBuffer) {
        return fetch(url)
          .then(function (res) {
            if (!res.ok) {
              throw new Error('mv_cost.tif GET ' + res.status + ' ' + res.statusText);
            }
            return res.arrayBuffer();
          })
          .then(function (buf) {
            return fromBuffer(buf);
          })
          .then(function (tif) {
            return tif.getImage(0);
          })
          .catch(function (fetchErr) {
            console.warn('[mv_cost.tif] fetch/fromArrayBuffer failed, trying GeoTIFF.fromUrl:', fetchErr);
            return GT.fromUrl(url).then(function (tiff) {
              return tiff.getImage(0);
            });
          });
      }
      return GT.fromUrl(url).then(function (tiff) {
        return tiff.getImage(0);
      });
    }

    return openMvCostGeoTiff()
      .then(function (image) {
        var bb = image.getBoundingBox(false);
        if (!bboxLooksLikeGeographicDegrees(bb)) {
          throw new Error(
            'bounding box outside geographic degree ranges: [' +
              bb.join(', ') +
              ']. Expected lon/lat in degrees.',
          );
        }
        var w = image.getWidth();
        var h = image.getHeight();
        var origin = image.getOrigin();
        var res = image.getResolution();
        var gdalNd = image.getGDALNoData();
        /** @type {number|null} */
        var nod = gdalNd != null && isFinite(Number(gdalNd)) ? Number(gdalNd) : null;
        /** Bbox-first lookup avoids subtle tiepoint/sign mismatches vs hex lat/lon. */
        /** @type {{west:number,south:number,east:number,north:number}|null} */
        var geoExtents =
          bb[2] > bb[0] && bb[3] > bb[1]
            ? { west: bb[0], south: bb[1], east: bb[2], north: bb[3] }
            : null;
        return image.readRasters({ samples: [0], interleave: false }).then(function (rasters) {
          mvCostRaster = {
            width: w,
            height: h,
            geoExtents: geoExtents,
            originLon: origin[0],
            originLat: origin[1],
            pixelW: res[0],
            pixelH: res[1],
            nodata: nod,
            /** @type {Float32Array|Float64Array|Int32Array} */
            data: rasters[0],
          };
          syncMvCostBoundsOverlay(geoExtents);
          redrawTerrainOverlay();
          redrawHexGrid();
          mvCostLoadFailureReason = '';
          return true;
        });
      })
      .catch(function (err) {
        mvCostRaster = null;
        mvCostLoadFailureReason =
          err && typeof err.message === 'string' && err.message.length ? err.message : String(err);
        console.error('[mv_cost.tif]', err);
        syncMvCostBoundsOverlay(null);
        redrawTerrainOverlay();
        redrawHexGrid();
        return false;
      });
  }

  /**
   * MP to enter the neighbour hex in direction dir (rounded mv_cost.tif at neighbour centre).
   * Vehicle / day–night are ignored; cost comes only from the raster.
   */
  function exitMoveCost(fromKey, dir, vehicle, isDay) {
    void vehicle;
    void isDay;
    var fq = parseHexKey(fromKey);
    var dq = AXIAL_NEIGHBORS[dir][0];
    var dr = AXIAL_NEIGHBORS[dir][1];
    var nKey = hexKey(fq[0] + dq, fq[1] + dr);
    if (!hexHasMvRaster(fromKey) || !hexHasMvRaster(nKey)) {
      return null;
    }
    return mvCostRoundedAtHexKey(nKey);
  }

  function segmentMoveCost(fromKey, toKey, vehicle, isDay) {
    var di = directionIndexBetweenHexes(fromKey, toKey);
    if (di < 0) return null;
    return exitMoveCost(fromKey, di, vehicle, isDay);
  }

  /** 0.5 MP (green) → 6 MP (red), clamped */
  function moveCostToCssColor(cost) {
    var cMin = 0.5;
    var cMax = 6;
    var c = typeof cost === 'number' ? cost : cMax;
    var t = (Math.min(Math.max(c, cMin), cMax) - cMin) / (cMax - cMin);
    var h = (120 * (1 - t)).toFixed(1);
    return 'hsl(' + h + ', 82%, 40%)';
  }

  /**
   * @typedef {{path: string[], anchorFirstEdges: number[], chainKeys: string[], startKey: string}} CompoundRoute
   */
  /** @returns {CompoundRoute|null} */
  function buildCompoundRoutePath(sel, isDay) {
    var dest = sel.destinationKey;
    if (!dest) return null;
    var ax = latLonToAxial(sel.lat, sel.lon);
    var sk = hexKey(ax.q, ax.r);
    var inter = sel.routeWaypointKeys ? sel.routeWaypointKeys.slice() : [];
    var chainKeys = inter.concat([dest]);
    /** @type {string[]} */
    var merged = [];
    /** @type {number[]} */
    var anchorFirstEdges = [];
    var prev = sk;
    var ai;
    for (ai = 0; ai < chainKeys.length; ai++) {
      var gk = chainKeys[ai];
      if (prev === gk) {
        if (merged.length === 0) merged = [gk];
        anchorFirstEdges.push(0);
        prev = gk;
        continue;
      }
      var seg = findRouteAStar(prev, gk, sel.vehicle, isDay);
      if (!seg) return null;
      if (merged.length === 0) {
        merged = seg.slice();
        anchorFirstEdges.push(0);
      } else {
        anchorFirstEdges.push(merged.length - 1);
        merged = merged.concat(seg.slice(1));
      }
      prev = gk;
    }

    if (anchorFirstEdges.length < chainKeys.length) {
      return null;
    }
    if (!merged.length) {
      return null;
    }
    return {
      path: merged,
      anchorFirstEdges: anchorFirstEdges,
      chainKeys: chainKeys,
      startKey: sk,
    };
  }

  function syncLegIndexAfterPathChange(sel) {
    if (!sel.routePath || sel.routePath.length === 0) return;
    var ax = latLonToAxial(sel.lat, sel.lon);
    var ck = hexKey(ax.q, ax.r);
    var ix = sel.routePath.indexOf(ck);
    if (ix >= 0) {
      sel.routeLegIndex = Math.min(ix, sel.routePath.length - 1);
    } else {
      sel.routeLegIndex = 0;
      sel.accumMovePoints = 0;
    }
  }

  /**
   * @returns {boolean}
   */
  function rebuildRouteFromAnchors(sel, resetProgress) {
    var isDay = exerciseIsDay(simInstantMs);
    var cmp = buildCompoundRoutePath(sel, isDay);
    if (!cmp || cmp.path.length < 2) {
      return false;
    }
    sel.routePath = cmp.path;
    if (resetProgress) {
      sel.routeLegIndex = 0;
      sel.accumMovePoints = 0;
    }
    syncLegIndexAfterPathChange(sel);
    return true;
  }

  function removeWaypointIntermediate(sel, indexInIntermediate) {
    GameApi.removeWaypoint(unitKey(sel.side, sel), indexInIntermediate)
      .then(function (res) {
        mergeServerUnit(res.unit);
        refreshAllUnitMarkerRouteButtons();
        setStatus(res.message || (res.ok ? sel.company + ' — waypoint removed' : 'Route cleared'));
        updateMarkerClasses();
      })
      .catch(function (err) {
        setStatus('Remove waypoint failed: ' + err.message);
      });
  }

  function simulatePlayTicksToArrival(sel) {
    if (!sel.routePath || sel.routePath.length === 0) {
      return Infinity;
    }
    if (sel.activity !== 'moving' || sel.routeLegIndex >= sel.routePath.length - 1) {
      return 0;
    }
    var path = sel.routePath;
    var idx = sel.routeLegIndex;
    var b = sel.accumMovePoints;
    var ticks = 0;
    var isDay = exerciseIsDay(simInstantMs);
    var safety = 0;
    while (idx < path.length - 1 && safety < 2500000) {
      safety++;
      ticks += 1;
      b += 1;
      var guard = 0;
      while (guard < 48 && idx < path.length - 1) {
        guard += 1;
        var curKey = path[idx];
        var nxKey = path[idx + 1];
        var di = directionIndexBetweenHexes(curKey, nxKey);
        if (di < 0) return Infinity;
        var cost = exitMoveCost(curKey, di, sel.vehicle, isDay);
        if (cost == null || !isFinite(cost)) return Infinity;
        if (b + 1e-9 >= cost) {
          b -= cost;
          idx += 1;
        } else {
          break;
        }
      }
    }
    if (idx < path.length - 1) {
      return Infinity;
    }
    return ticks;
  }

  function estimatedArrivalSimMs(sel) {
    var ticks = simulatePlayTicksToArrival(sel);
    if (!isFinite(ticks) || ticks < 0) {
      return null;
    }
    return simInstantMs + ticks * SIM_TICK_MS;
  }

  function axialCubeDistance(q1, r1, q2, r2) {
    var dq = q2 - q1;
    var dr = r2 - r1;
    var ds = -q2 - r2 - (-q1 - r1);
    return (Math.abs(dq) + Math.abs(dr) + Math.abs(ds)) / 2;
  }

  function pathfindHeuristic(fromKey, goalKey, vehicle, isDay) {
    void vehicle;
    void isDay;
    var a = parseHexKey(fromKey);
    var b = parseHexKey(goalKey);
    var d = axialCubeDistance(a[0], a[1], b[0], b[1]);
    var ck = mvCostRoundedAtHexKey(fromKey);
    var mn = ck != null && ck > 0 && isFinite(ck) ? ck : 1;
    return d * mn;
  }

  /**
   * @returns {string[]|null}
   */
  function findRouteAStar(startKey, goalKey, vehicle, isDay) {
    if (!hexHasMvRaster(startKey) || !hexHasMvRaster(goalKey)) return null;
    if (startKey === goalKey) return [startKey];

    /** @type {Object.<string, number>} */
    var gScore = {};
    /** @type {Object.<string, string>} */
    var came = {};
    var openKeys = {};

    /** @type {Array<{ key: string, f: number }>} */
    var open = [];

    function heur(k) {
      return pathfindHeuristic(k, goalKey, vehicle, isDay);
    }

    gScore[startKey] = 0;
    openKeys[startKey] = true;
    open.push({ key: startKey, f: heur(startKey) });

    function popCheapest() {
      var mi = 0;
      var i;
      for (i = 1; i < open.length; i++) {
        if (open[i].f < open[mi].f) mi = i;
      }
      return open.splice(mi, 1)[0];
    }

    while (open.length > 0) {
      var cur = popCheapest().key;
      delete openKeys[cur];
      if (cur === goalKey) break;
      var cq = parseHexKey(cur);

      var di;
      for (di = 0; di < 6; di++) {
        var step = exitMoveCost(cur, di, vehicle, isDay);
        if (step == null || !isFinite(step)) continue;
        var nq = cq[0] + AXIAL_NEIGHBORS[di][0];
        var nr = cq[1] + AXIAL_NEIGHBORS[di][1];
        var nk = hexKey(nq, nr);
        var tentative = (gScore[cur] || 0) + step;
        if (gScore[nk] !== undefined && tentative >= gScore[nk]) continue;
        gScore[nk] = tentative;
        came[nk] = cur;
        if (!(nk in openKeys)) {
          openKeys[nk] = true;
          open.push({ key: nk, f: tentative + heur(nk) });
        } else {
          var oi;
          for (oi = 0; oi < open.length; oi++) {
            if (open[oi].key === nk) {
              open[oi].f = tentative + heur(nk);
              break;
            }
          }
        }
      }
    }

    if (gScore[goalKey] === undefined) return null;
    /** @type {string[]} */
    var seq = [];
    var w = goalKey;
    var hop = 0;
    for (;;) {
      hop++;
      if (hop > 250000) return null;
      seq.push(w);
      if (w === startKey) break;
      if (!Object.prototype.hasOwnProperty.call(came, w)) return null;
      w = came[w];
    }
    seq.reverse();
    if (seq[0] !== startKey || seq[seq.length - 1] !== goalKey) return null;
    return seq;
  }

  function exerciseIsDay(simMs) {
    var p = fuldaWallParts(simMs);
    var st = solarTimesFuldaWallDay(p.y, p.mo, p.day);
    if (!st) return true;
    return simMs >= st.sunrise.getTime() && simMs < st.sunset.getTime();
  }

  function directionIndexBetweenHexes(fromKey, toKey) {
    var f = parseHexKey(fromKey);
    var t = parseHexKey(toKey);
    var dq = t[0] - f[0];
    var dr = t[1] - f[1];
    var di;
    for (di = 0; di < 6; di++) {
      if (AXIAL_NEIGHBORS[di][0] === dq && AXIAL_NEIGHBORS[di][1] === dr) return di;
    }
    return -1;
  }

  function escapeHtmlLite(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  var SPOTTED_EYE_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/>' +
    '<circle cx="12" cy="12" r="3"/>' +
    '</svg>';

  function unitVerboseLabel(u) {
    return u.company + ' — ' + u.battalion + ' (' + u.side + ')';
  }

  function clearAcquisitionSilenceForTargetHex(target, hexKey) {
    if (!hexKey) return;
    var prefix = unitKey(target.side, target) + '|' + hexKey + '|';
    var k;
    for (k in acquisitionSilenced) {
      if (Object.prototype.hasOwnProperty.call(acquisitionSilenced, k) && k.indexOf(prefix) === 0) {
        delete acquisitionSilenced[k];
      }
    }
  }

  function acquisitionSilenceKey(target, enteredHexKey, spotter) {
    return unitKey(target.side, target) + '|' + enteredHexKey + '|' + unitKey(spotter.side, spotter);
  }

  /** When mover leaves fromHexKey: clear LOS silences (target=mover) and proximity silences (spotter=mover, hex left). */
  function clearAcquisitionSilenceWhenMoverLeavesHex(mover, fromHexKey) {
    clearAcquisitionSilenceForTargetHex(mover, fromHexKey);
    var moverKey = unitKey(mover.side, mover);
    var tail = '|' + fromHexKey + '|' + moverKey;
    var dels = [];
    var k;
    for (k in acquisitionSilenced) {
      if (!Object.prototype.hasOwnProperty.call(acquisitionSilenced, k)) continue;
      if (k.length >= tail.length && k.slice(-tail.length) === tail) dels.push(k);
    }
    var di;
    for (di = 0; di < dels.length; di++) {
      delete acquisitionSilenced[dels[di]];
    }
  }

  function initialBearingDegrees(lat1, lon1, lat2, lon2) {
    var φ1 = (lat1 * Math.PI) / 180;
    var φ2 = (lat2 * Math.PI) / 180;
    var Δλ = ((lon2 - lon1) * Math.PI) / 180;
    var y = Math.sin(Δλ) * Math.cos(φ2);
    var x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
    var θ = Math.atan2(y, x);
    return ((θ * 180) / Math.PI + 360) % 360;
  }

  function cardinalFromBearingDegrees(deg) {
    var names = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    var ix = Math.round(deg / 45) % 8;
    return names[ix];
  }

  function travelCardinalDirection(fromHexKey, toHexKey) {
    var f = parseHexKey(fromHexKey);
    var t = parseHexKey(toHexKey);
    var ll0 = axialCenterLatLon(f[0], f[1]);
    var ll1 = axialCenterLatLon(t[0], t[1]);
    return cardinalFromBearingDegrees(initialBearingDegrees(ll0[0], ll0[1], ll1[0], ll1[1]));
  }

  var CARDINAL_WORDS = {
    N: 'north',
    NE: 'northeast',
    E: 'east',
    SE: 'southeast',
    S: 'south',
    SW: 'southwest',
    W: 'west',
    NW: 'northwest',
  };

  function formatMgrsSixDigit(lat, lon) {
    var M = typeof window !== 'undefined' && window.mgrs ? window.mgrs : null;
    if (!M || typeof M.forward !== 'function') {
      return '';
    }
    try {
      return M.forward([lon, lat], 3);
    } catch (err) {
      console.warn('MGRS forward failed', err);
      return '';
    }
  }

  function acquisitionQueueHasEvent(target, enteredHexKey, spotter, spotKind) {
    var qi;
    var sk = spotKind || 'los';
    for (qi = 0; qi < acquisitionQueue.length; qi++) {
      var q = acquisitionQueue[qi];
      if (
        unitKey(q.target.side, q.target) === unitKey(target.side, target) &&
        q.enteredHexKey === enteredHexKey &&
        unitKey(q.spotter.side, q.spotter) === unitKey(spotter.side, spotter) &&
        (q.spotKind || 'los') === sk
      ) {
        return true;
      }
    }
    return false;
  }

  function enqueueVisualAcquisitionIfAny(target, fromHexKey, enteredHexKey) {
    var vi;
    for (vi = 0; vi < units.length; vi++) {
      var spotter = units[vi];
      if (spotter.side === target.side) continue;
      var losCells = hexKeysLineOfSightAreaForUnit(spotter);
      if (!Object.prototype.hasOwnProperty.call(losCells, enteredHexKey)) continue;
      var silKey = acquisitionSilenceKey(target, enteredHexKey, spotter);
      if (acquisitionSilenced[silKey]) continue;
      if (acquisitionQueueHasEvent(target, enteredHexKey, spotter, 'los')) continue;
      acquisitionQueue.push({
        spotter: spotter,
        target: target,
        enteredHexKey: enteredHexKey,
        fromHexKey: fromHexKey,
        spotKind: 'los',
      });
    }
  }

  /** Mover enters enteredHexKey: proximity acquisition vs halted enemies within PROXIMITY_ACQUISITION_HEX_DISTANCE. */
  function enqueueProximityAcquisitionIfAny(mover, fromHexKey, enteredHexKey) {
    var vi;
    for (vi = 0; vi < units.length; vi++) {
      var enemy = units[vi];
      if (enemy.side === mover.side) continue;
      if (enemy.activity !== 'halted') continue;
      var axE = latLonToAxial(enemy.lat, enemy.lon);
      var enemyHex = hexKey(axE.q, axE.r);
      if (hexKeyDistance(enteredHexKey, enemyHex) > PROXIMITY_ACQUISITION_HEX_DISTANCE) continue;
      var silKey = acquisitionSilenceKey(enemy, enteredHexKey, mover);
      if (acquisitionSilenced[silKey]) continue;
      if (acquisitionQueueHasEvent(enemy, enteredHexKey, mover, 'proximity')) continue;
      acquisitionQueue.push({
        spotter: mover,
        target: enemy,
        enteredHexKey: enteredHexKey,
        fromHexKey: fromHexKey,
        spotKind: 'proximity',
      });
    }
  }

  function setPlayButtonAcquisitionDisabled(disabled) {
    var playBtn = document.getElementById('timebar-play');
    if (playBtn) playBtn.disabled = !!disabled;
  }

  function resetAcquisitionModalPanelLayout() {
    var root = document.getElementById('acquisition-modal');
    if (!root) return;
    var panel = root.querySelector('.game-modal-panel');
    if (!panel) return;
    panel.style.position = '';
    panel.style.left = '';
    panel.style.top = '';
    panel.style.right = '';
    panel.style.bottom = '';
    panel.style.margin = '';
    panel.style.transform = '';
    panel.style.zIndex = '';
    var h = document.getElementById('acquisition-modal-title');
    if (h) h.style.cursor = '';
  }

  function hideAcquisitionModal() {
    var el = document.getElementById('acquisition-modal');
    if (el) {
      el.hidden = true;
    }
    resetAcquisitionModalPanelLayout();
  }

  function initAcquisitionModalDrag() {
    var root = document.getElementById('acquisition-modal');
    var handle = document.getElementById('acquisition-modal-title');
    if (!root || !handle || handle.getAttribute('data-drag-init') === '1') return;
    handle.setAttribute('data-drag-init', '1');
    var panel = root.querySelector('.game-modal-panel');
    if (!panel) return;

    var drag = null;

    function onPointerMove(e) {
      if (!drag) return;
      var clientX = e.clientX != null ? e.clientX : e.touches && e.touches[0] ? e.touches[0].clientX : null;
      var clientY = e.clientY != null ? e.clientY : e.touches && e.touches[0] ? e.touches[0].clientY : null;
      if (clientX == null || clientY == null) return;
      if (e.cancelable) e.preventDefault();
      var dx = clientX - drag.x0;
      var dy = clientY - drag.y0;
      panel.style.left = drag.l0 + dx + 'px';
      panel.style.top = drag.t0 + dy + 'px';
    }

    function onPointerUp() {
      if (!drag) return;
      drag = null;
      handle.style.cursor = '';
      window.removeEventListener('mousemove', onPointerMove);
      window.removeEventListener('mouseup', onPointerUp);
      window.removeEventListener('touchmove', onPointerMove);
      window.removeEventListener('touchend', onPointerUp);
      window.removeEventListener('touchcancel', onPointerUp);
    }

    function onPointerDown(e) {
      if (root.hidden) return;
      if (e.type === 'mousedown' && e.button !== 0) return;
      var clientX = e.clientX != null ? e.clientX : e.touches && e.touches[0] ? e.touches[0].clientX : null;
      var clientY = e.clientY != null ? e.clientY : e.touches && e.touches[0] ? e.touches[0].clientY : null;
      if (clientX == null || clientY == null) return;
      var tgt = e.target;
      if (tgt && typeof tgt.closest === 'function' && tgt.closest('button')) return;
      var r = panel.getBoundingClientRect();
      panel.style.position = 'fixed';
      panel.style.left = r.left + 'px';
      panel.style.top = r.top + 'px';
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
      panel.style.margin = '0';
      panel.style.transform = 'none';
      panel.style.zIndex = '1';
      drag = { x0: clientX, y0: clientY, l0: r.left, t0: r.top };
      handle.style.cursor = 'grabbing';
      if (e.cancelable) e.preventDefault();
      window.addEventListener('mousemove', onPointerMove);
      window.addEventListener('mouseup', onPointerUp);
      window.addEventListener('touchmove', onPointerMove, { passive: false });
      window.addEventListener('touchend', onPointerUp);
      window.addEventListener('touchcancel', onPointerUp);
    }

    handle.addEventListener('mousedown', onPointerDown);
    handle.addEventListener('touchstart', onPointerDown, { passive: false });
  }

  function hideSpotReportModal() {
    var el = document.getElementById('spotreport-modal');
    if (el) {
      el.hidden = true;
    }
  }

  function showSpotReportModal(target, fromHexKey, toHexKey, spotKind) {
    var body = document.getElementById('spotreport-modal-body');
    var modal = document.getElementById('spotreport-modal');
    if (!body || !modal) return;
    var loc = formatMgrsSixDigit(target.lat, target.lon);
    var sk = spotKind || 'los';
    var activityHtml;
    if (sk === 'proximity') {
      activityHtml = escapeHtmlLite('halted');
    } else {
      var card = travelCardinalDirection(fromHexKey, toHexKey);
      var cardWord = CARDINAL_WORDS[card] || card;
      activityHtml = escapeHtmlLite('traveling — ' + cardWord);
    }
    var timeStr = formatExerciseMainLine(simInstantMs);
    body.innerHTML =
      '<p><strong>Size:</strong> </p>' +
      '<p><strong>Activity:</strong> ' +
      activityHtml +
      '</p>' +
      '<p><strong>Location:</strong> ' +
      escapeHtmlLite(loc || '—') +
      '</p>' +
      '<p><strong>Time:</strong> ' +
      escapeHtmlLite(timeStr) +
      '</p>' +
      '<p><strong>Equipment:</strong> </p>';
    modal.hidden = false;
  }

  function showSpotReportFromApi(report) {
    var body = document.getElementById('spotreport-modal-body');
    var modal = document.getElementById('spotreport-modal');
    if (!body || !modal || !report) return;
    body.innerHTML =
      '<p><strong>Size:</strong> ' +
      escapeHtmlLite(report.size || '') +
      '</p>' +
      '<p><strong>Activity:</strong> ' +
      escapeHtmlLite(report.activity || '') +
      '</p>' +
      '<p><strong>Location:</strong> ' +
      escapeHtmlLite(report.location || '—') +
      '</p>' +
      '<p><strong>Time:</strong> ' +
      escapeHtmlLite(report.time || '') +
      '</p>' +
      '<p><strong>Equipment:</strong> ' +
      escapeHtmlLite(report.equipment || '') +
      '</p>';
    modal.hidden = false;
  }

  function resolveAcquisitionQueueHead(ev, confirm) {
    GameApi.acquisitionResolve({
      spotterKey: unitKey(ev.spotter.side, ev.spotter),
      targetKey: unitKey(ev.target.side, ev.target),
      enteredHexKey: ev.enteredHexKey,
      fromHexKey: ev.fromHexKey,
      spotKind: ev.spotKind || 'los',
      confirm: confirm,
    })
      .then(function (res) {
        if (confirm && res.spotReport) {
          var target = getUnitByKey(ev.target.side ? unitKey(ev.target.side, ev.target) : ev.target.key);
          if (target) {
            target.spotted = true;
            refreshUnitMarkerIcon(target);
          }
          showSpotReportFromApi(res.spotReport);
          var okBtn = document.getElementById('spotreport-ok');
          function onSpotOk() {
            if (okBtn) okBtn.removeEventListener('click', onSpotOk);
            hideSpotReportModal();
            if (acquisitionQueue.length) acquisitionQueue.shift();
            hideAcquisitionModal();
            acquisitionDrainStep();
          }
          if (okBtn) okBtn.addEventListener('click', onSpotOk);
          return;
        }
        if (acquisitionQueue.length) acquisitionQueue.shift();
        hideAcquisitionModal();
        acquisitionDrainStep();
      })
      .catch(function (err) {
        setStatus('Acquisition resolve failed: ' + err.message);
        if (acquisitionQueue.length) acquisitionQueue.shift();
        hideAcquisitionModal();
        acquisitionDrainStep();
      });
  }

  function acquisitionDrainStep() {
    var ev = acquisitionQueue[0];
    if (!ev) {
      acquisitionFlowActive = false;
      setPlayButtonAcquisitionDisabled(false);
      return;
    }
    resetAcquisitionModalPanelLayout();
    map.panTo(L.latLng(ev.target.lat, ev.target.lon));
    var body = document.getElementById('acquisition-modal-body');
    var modal = document.getElementById('acquisition-modal');
    if (!body || !modal) {
      acquisitionQueue.length = 0;
      acquisitionFlowActive = false;
      setPlayButtonAcquisitionDisabled(false);
      return;
    }
    body.innerHTML =
      '<p><strong>Spotting Unit:</strong> ' +
      escapeHtmlLite(unitVerboseLabel(ev.spotter)) +
      '</p>' +
      '<p><strong>Target Unit:</strong> ' +
      escapeHtmlLite(unitVerboseLabel(ev.target)) +
      '</p>';
    modal.hidden = false;

    var denyBtn = document.getElementById('acquisition-deny');
    var confBtn = document.getElementById('acquisition-confirm');

    function cleanupAcquisitionButtons() {
      if (denyBtn) denyBtn.removeEventListener('click', onDeny);
      if (confBtn) confBtn.removeEventListener('click', onConfirm);
    }

    function onDeny() {
      cleanupAcquisitionButtons();
      resolveAcquisitionQueueHead(ev, false);
    }

    function onConfirm() {
      cleanupAcquisitionButtons();
      hideAcquisitionModal();
      resolveAcquisitionQueueHead(ev, true);
    }

    if (denyBtn) denyBtn.addEventListener('click', onDeny);
    if (confBtn) confBtn.addEventListener('click', onConfirm);
  }

  function beginAcquisitionFlowIfQueued() {
    if (!acquisitionQueue.length) return;
    acquisitionFlowActive = true;
    setPlayButtonAcquisitionDisabled(true);
    acquisitionDrainStep();
  }

  function refreshUnitMarkerIcon(u) {
    var mk = markerByKey[unitKey(u.side, u)];
    if (!mk) return;
    mk.setLatLng(L.latLng(u.lat, u.lon));
    var showClr =
      selectionMode === 'company' &&
      selectionCompanyKey === unitKey(u.side, u) &&
      !!(u.destinationKey || (u.routeWaypointKeys && u.routeWaypointKeys.length > 0));
    var leftH2 = buildCombatSidebarHtml(u.totalDirectFire, u.totalIndirectFire, u.totalCloseCombat);
    var rightH2 = buildEquipmentSidebarHtml(u.equipmentSpecs || []);
    mk.setIcon(makeMarkerIcon(u.side, SYMBOL_SIZE, u.activity, showClr, u.spotted, leftH2, rightH2));
    bindUnitRouteClearButton(mk, u);
  }

  function refreshAllUnitMarkerRouteButtons() {
    units.forEach(function (u) {
      var k = unitKey(u.side, u);
      if (markerByKey[k] && map.hasLayer(markerByKey[k])) {
        refreshUnitMarkerIcon(u);
      }
    });
  }

  function bindUnitRouteClearButton(mk, u) {
    setTimeout(function () {
      var el = mk.getElement();
      if (!el) return;
      var btn = el.querySelector('.route-clear-unit-btn');
      if (!btn) return;
      btn.onmousedown = function (ev) {
        L.DomEvent.stopPropagation(ev);
      };
      btn.onclick = function (ev) {
        L.DomEvent.stopPropagation(ev);
        if (selectionMode === 'company' && unitKey(u.side, u) === selectionCompanyKey) {
          clearCompanyRoute(u);
        }
      };
    }, 0);
  }

  var hexLayerGroup = L.layerGroup({ pane: 'hexPane' }).addTo(map);
  var terrainOverlayLayerGroup = L.layerGroup({ pane: 'terrainOverlayPane' }).addTo(map);
  var zocLayerGroup = L.layerGroup({ pane: 'zocPane' }).addTo(map);
  var losLayerGroup = L.layerGroup({ pane: 'losPane' }).addTo(map);
  var routeLayerGroup = L.layerGroup({ pane: 'routePane' }).addTo(map);

  function clearRouteOverlay() {
    routeLayerGroup.clearLayers();
  }

  function applyWaypointMarkerDrag(sel, kind, viaIndex, latlng) {
    if (!(selectionMode === 'company' && unitKey(sel.side, sel) === selectionCompanyKey)) {
      return;
    }
    GameApi.waypoint(unitKey(sel.side, sel), kind, viaIndex, latlng.lat, latlng.lng)
      .then(function (res) {
        mergeServerUnit(res.unit);
        refreshAllUnitMarkerRouteButtons();
        if (res.ok) {
          setStatus(sel.company + ' — route updated');
        } else if (res.message) {
          setStatus(res.message);
        }
        updateMarkerClasses();
      })
      .catch(function (err) {
        setStatus('Waypoint update failed: ' + err.message);
      });
  }

  function segmentBearingDeg(lat1, lon1, lat2, lon2) {
    var sc = metersPerDegree((lat1 + lat2) / 2);
    var dx = (lon2 - lon1) * sc.lon;
    var dy = (lat2 - lat1) * sc.lat;
    return (Math.atan2(dx, dy) * 180) / Math.PI;
  }

  function redrawRouteOverlay() {
    routeLayerGroup.clearLayers();
    if (selectionMode !== 'company' || !selectionCompanyKey) return;

    var sel = null;
    var ui;
    for (ui = 0; ui < units.length; ui++) {
      if (unitKey(units[ui].side, units[ui]) === selectionCompanyKey) {
        sel = units[ui];
        break;
      }
    }
    if (!sel || !sel.routePath || sel.routePath.length < 2) return;

    var leg = typeof sel.routeLegIndex === 'number' ? sel.routeLegIndex : 0;
    leg = Math.max(0, Math.min(leg, sel.routePath.length - 1));
    var sliceKeys = sel.routePath.slice(leg);
    var latLngs = sliceKeys.map(function (hk) {
      var qr = parseHexKey(hk);
      var ll = axialCenterLatLon(qr[0], qr[1]);
      return [ll[0], ll[1]];
    });

    function drawRouteSegments(costs) {
      var sj;
      for (sj = 0; sj < latLngs.length - 1; sj++) {
        var cst = costs && costs[sj] != null ? costs[sj] : 6;
        var col = moveCostToCssColor(typeof cst === 'number' ? cst : 6);
        var segPair = [latLngs[sj], latLngs[sj + 1]];
      var pl = L.polyline(segPair, {
        color: col,
        weight: 6,
        opacity: 0.92,
        lineCap: 'round',
        lineJoin: 'round',
        interactive: false,
        bubblingMouseEvents: false,
        pane: 'routePane',
      });
      pl.addTo(routeLayerGroup);
      var mx = [(latLngs[sj][0] + latLngs[sj + 1][0]) / 2, (latLngs[sj][1] + latLngs[sj + 1][1]) / 2];
      var brgFwd = segmentBearingDeg(latLngs[sj][0], latLngs[sj][1], latLngs[sj + 1][0], latLngs[sj + 1][1]);
      var brg = brgFwd + 180;
      var arrowHtml = '<div class="route-arrow-glyph">\u25bc</div>';
      L.marker(mx, {
        icon: L.divIcon({
          className: 'route-arrow-marker',
          html:
            '<div class="route-arrow-wrap" style="transform:rotate(' +
            brg +
            'deg)">' +
            arrowHtml +
            '</div>',
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        }),
        pane: 'routePane',
        interactive: false,
      }).addTo(routeLayerGroup);
      }
    }

    GameApi.segmentCosts(sliceKeys)
      .then(function (data) {
        drawRouteSegments(data.costs);
      })
      .catch(function () {
        drawRouteSegments(null);
      });

    /** Intermediate waypoint handles */
    var wk;
    var vias = sel.routeWaypointKeys ? sel.routeWaypointKeys : [];
    for (wk = 0; wk < vias.length; wk++) {
      var qw = parseHexKey(vias[wk]);
      var lw = axialCenterLatLon(qw[0], qw[1]);
      var wm = L.marker(L.latLng(lw[0], lw[1]), {
        draggable: true,
        pane: 'routePane',
        riseOnHover: true,
      });
      wm.setIcon(
        L.divIcon({
          className: 'route-via-marker',
          html:
            '<div class="route-via-wrap">' +
            '<div class="route-via-inner"><span class="route-via-num">' +
            String(wk + 1) +
            '</span></div>' +
            '<button type="button" class="route-wp-remove-btn" title="Remove waypoint" aria-label="Remove waypoint">\u2715</button>' +
            '</div>',
          iconSize: [40, 28],
          iconAnchor: [20, 14],
        }),
      );
      wm.bindTooltip(sel.company + ' · via ' + String(wk + 1));
      wm.addTo(routeLayerGroup);
      (function (wk2) {
        wm.on('mousedown', function (e) {
          var t = e.originalEvent && e.originalEvent.target;
          if (t && t.closest && t.closest('.route-wp-remove-btn')) {
            L.DomEvent.stopPropagation(e);
          }
        });
        wm.on('click', function (e) {
          var t = e.originalEvent && e.originalEvent.target;
          if (t && t.closest && t.closest('.route-wp-remove-btn')) {
            L.DomEvent.stopPropagation(e);
            L.DomEvent.preventDefault(e.originalEvent);
            removeWaypointIntermediate(sel, wk2);
          }
        });
        wm.on('dragend', function (ev) {
          applyWaypointMarkerDrag(sel, 'via', wk2, ev.target.getLatLng());
        });
      })(wk);
    }

    var destKey =
      sel.destinationKey ||
      sliceKeys[sliceKeys.length - 1];
    var qd = parseHexKey(destKey);
    var destLL = axialCenterLatLon(qd[0], qd[1]);
    var etaMs = estimatedArrivalSimMs(sel);
    var etaLine =
      etaMs != null ? formatExerciseMainLine(etaMs) : '(no ETA)';
    var destMk = L.marker(L.latLng(destLL[0], destLL[1]), {
      draggable: true,
      pane: 'routePane',
      riseOnHover: true,
    });
    destMk.setIcon(
      L.divIcon({
        className: 'route-dest-marker',
        html:
          '<div class="route-dest-compact">' +
          '<div class="route-dest-pushpin" title="Final destination"></div>' +
          '<div class="route-dest-compact-text">' +
          '<span class="route-dest-compact-co">' +
          escapeHtmlLite(sel.company) +
          '</span>' +
          '<span class="route-dest-compact-eta">ETA ' +
          escapeHtmlLite(etaLine) +
          '</span>' +
          '</div>' +
          '</div>',
        iconSize: [112, 52],
        iconAnchor: [18, 46],
      }),
    );
    destMk.bindTooltip('Drag to move final destination');
    destMk.on('dragend', function (ev2) {
      applyWaypointMarkerDrag(sel, 'dest', -1, ev2.target.getLatLng());
    });
    destMk.addTo(routeLayerGroup);
  }

  function clearCompanyRoute(u) {
    if (!u) return;
    GameApi.clearRoute(unitKey(u.side, u))
      .then(function (res) {
        mergeServerUnit(res.unit);
        refreshAllUnitMarkerRouteButtons();
        updateMarkerClasses();
        setStatus('Route cleared');
      })
      .catch(function (err) {
        setStatus('Clear route failed: ' + err.message);
      });
  }

  function assignMoveOrder(sel, goalKey) {
    GameApi.moveOrder(unitKey(sel.side, sel), goalKey, false)
      .then(function (res) {
        mergeServerUnit(res.unit);
        refreshAllUnitMarkerRouteButtons();
        if (res.ok) {
          setStatus(sel.company + ' moving — destination ' + goalKey);
        } else {
          setStatus(res.message || 'No route.');
        }
        updateMarkerClasses();
      })
      .catch(function (err) {
        setStatus('Move order failed: ' + err.message);
      });
  }

  function extendRouteWithNewDestination(sel, goalKey) {
    if (!sel.destinationKey) {
      assignMoveOrder(sel, goalKey);
      return;
    }
    if (goalKey === sel.destinationKey) {
      setStatus('Already the final destination hex.');
      return;
    }
    GameApi.moveOrder(unitKey(sel.side, sel), goalKey, true)
      .then(function (res) {
        mergeServerUnit(res.unit);
        refreshAllUnitMarkerRouteButtons();
        if (res.ok) {
          setStatus(
            sel.company +
              ' — new destination ' +
              goalKey +
              ' (previous goal is now via ' +
              String((res.unit.routeWaypointKeys || []).length) +
              ')',
          );
        } else {
          setStatus(res.message || 'No route.');
        }
        updateMarkerClasses();
      })
      .catch(function (err) {
        setStatus('Extend route failed: ' + err.message);
      });
  }

  function redrawHexGrid() {
    hexLayerGroup.clearLayers();
    if (map.getZoom() < HEX_GRID_MIN_ZOOM) {
      redrawTerrainOverlay();
      return;
    }
    var b = map.getBounds();
    var scale = metersPerDegree(hexOriginLat);

    var pad = FLAT_TO_FLAT_M * 2;
    var xMin = (b.getWest() - hexOriginLon) * scale.lon - pad;
    var xMax = (b.getEast() - hexOriginLon) * scale.lon + pad;
    var yMin = (b.getSouth() - hexOriginLat) * scale.lat - pad;
    var yMax = (b.getNorth() - hexOriginLat) * scale.lat + pad;

    var horiz = R_M * Math.sqrt(3);
    var vert = R_M * 1.5;
    var qMin = Math.floor(xMin / horiz) - 2;
    var qMax = Math.ceil(xMax / horiz) + 2;
    var rMin = Math.floor(yMin / vert) - 2;
    var rMax = Math.ceil(yMax / vert) + 2;

    var style = {
      color: '#335',
      weight: 1,
      opacity: 0.55,
      fill: false,
      interactive: false,
    };

    var q;
    var r;
    for (q = qMin; q <= qMax; q++) {
      for (r = rMin; r <= rMax; r++) {
        var center = axialToXY(q, r);
        if (center[0] < xMin || center[0] > xMax || center[1] < yMin || center[1] > yMax) {
          continue;
        }
        var cenLL = axialCenterLatLon(q, r);
        if (!hexCenterInPlay(cenLL[0], cenLL[1])) {
          continue;
        }
        var ring = hexVerticesMeters(center[0], center[1]).map(function (m) {
          return metersToLatLon(m, hexOriginLat, hexOriginLon, scale);
        });
        ring.push(ring[0]);
        L.polygon([ring], style).addTo(hexLayerGroup);
      }
    }
    redrawTerrainOverlay();
  }

  function buildTerrainOverlayHtml(q, r, mvRounded, rawMv) {
    var key = q + ',' + r;
    var rawTxt = rawMv != null && isFinite(Number(rawMv)) ? Number(rawMv).toFixed(3) : '—';
    return (
      '<div class="terrain-ov-card">' +
      '<div class="terrain-ov-coord">' +
      key +
      '</div>' +
      '<div class="terrain-ov-row">MP <strong>' +
      mvRounded +
      '</strong></div>' +
      '<div class="terrain-ov-row terrain-ov-raw">' +
      'raw ' +
      rawTxt +
      '</div>' +
      '</div>'
    );
  }

  function redrawTerrainOverlay() {
    terrainOverlayLayerGroup.clearLayers();
    if (!terrainOverlayEnabled || !mvCostMeta.loaded) {
      return;
    }
    if (map.getZoom() < HEX_GRID_MIN_ZOOM) {
      return;
    }
    var b = map.getBounds();
    GameApi.terrainHexes(b.getWest(), b.getSouth(), b.getEast(), b.getNorth())
      .then(function (data) {
        var z = map.getZoom();
        var baseW = Math.max(56, Math.min(120, 44 + (z - 10) * 10));
        var baseH = Math.max(36, Math.min(68, 30 + (z - 10) * 5));
        (data.hexes || []).forEach(function (h) {
          var html = buildTerrainOverlayHtml(h.q, h.r, h.mv_rounded, h.mv_raw);
          var latlng = axialCenterLatLon(h.q, h.r);
          L.marker(L.latLng(latlng[0], latlng[1]), {
            pane: 'terrainOverlayPane',
            interactive: false,
            icon: L.divIcon({
              className: 'terrain-hex-overlay',
              html: html,
              iconSize: [baseW, baseH],
              iconAnchor: [baseW / 2, baseH / 2],
            }),
          }).addTo(terrainOverlayLayerGroup);
        });
      })
      .catch(function (err) {
        console.warn('terrain overlay', err);
      });
  }

  function setTerrainOverlayEnabled(on) {
    terrainOverlayEnabled = !!on;
    var btn = document.getElementById('terrain-overlay-toggle');
    if (btn) {
      btn.setAttribute('aria-pressed', terrainOverlayEnabled ? 'true' : 'false');
      btn.classList.toggle('terrain-overlay-toggle--on', terrainOverlayEnabled);
    }
    redrawTerrainOverlay();
  }

  (function initTerrainOverlayToggle() {
    var btn = document.getElementById('terrain-overlay-toggle');
    if (btn) {
      btn.addEventListener('click', function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        ev.stopPropagation();
        setTerrainOverlayEnabled(!terrainOverlayEnabled);
      });
    }
  })();

  map.on('moveend zoomend', redrawHexGrid);

  function buildBattalionGroupsMap() {
    battalionGroupsMap = {};
    units.forEach(function (u) {
      var bk = battalionKey(u.side, u);
      if (!battalionGroupsMap[bk]) {
        battalionGroupsMap[bk] = {
          battalionKey: bk,
          battalion: u.battalion,
          side: u.side,
          units: [],
        };
      }
      battalionGroupsMap[bk].units.push(u);
    });
  }

  function centroidLatLon(us) {
    var sl = 0;
    var sn = 0;
    var i;
    for (i = 0; i < us.length; i++) {
      sl += us[i].lat;
      sn += us[i].lon;
    }
    var n = us.length || 1;
    return [sl / n, sn / n];
  }

  function syncBattalionAggregateMarkers() {
    var bk;
    for (bk in battalionMarkerByKey) {
      if (!Object.prototype.hasOwnProperty.call(battalionMarkerByKey, bk)) continue;
      var grp = battalionGroupsMap[bk];
      if (!grp || grp.units.length < 2) continue;
      var cen = centroidLatLon(grp.units);
      var m = battalionMarkerByKey[bk];
      m.setLatLng(cen);
      m.setIcon(
        makeMarkerIcon(grp.side, BATTALION_SYMBOL_SIZE, summarizeBattalionActivity(grp.units)),
      );
    }
  }

  function companiesOverlapPixels(us) {
    if (us.length < 2) return false;
    var pts = us.map(function (u) {
      return map.latLngToContainerPoint(L.latLng(u.lat, u.lon));
    });
    var i;
    var j;
    var limit = COMPANY_OVERLAP_PIXELS;
    for (i = 0; i < pts.length; i++) {
      for (j = i + 1; j < pts.length; j++) {
        if (pts[i].distanceTo(pts[j]) < limit) {
          return true;
        }
      }
    }
    return false;
  }

  /** Decide per battalion: company markers vs aggregated battalion marker (screen-pixel overlap). */
  function refreshMarkerDisplay() {
    if (Object.keys(markerByKey).length === 0) {
      return;
    }

    battalionOverlapAgg = {};
    var bk;
    for (bk in battalionMarkerByKey) {
      if (Object.prototype.hasOwnProperty.call(battalionMarkerByKey, bk)) {
        battalionMarkerByKey[bk].remove();
      }
    }
    units.forEach(function (u) {
      markerByKey[unitKey(u.side, u)].remove();
    });

    for (bk in battalionGroupsMap) {
      if (!Object.prototype.hasOwnProperty.call(battalionGroupsMap, bk)) continue;
      var grp = battalionGroupsMap[bk];
      var useAgg = grp.units.length >= 2 && companiesOverlapPixels(grp.units);
      battalionOverlapAgg[bk] = useAgg;
      var bi;
      if (useAgg) {
        var bnM = battalionMarkerByKey[bk];
        if (bnM) {
          bnM.addTo(map);
        }
      } else {
        for (bi = 0; bi < grp.units.length; bi++) {
          markerByKey[unitKey(grp.side, grp.units[bi])].addTo(map);
        }
      }
    }
    updateMarkerClasses();
  }

  function createBattalionAggregateMarkers() {
    var bk;
    for (bk in battalionMarkerByKey) {
      if (Object.prototype.hasOwnProperty.call(battalionMarkerByKey, bk)) {
        battalionMarkerByKey[bk].remove();
      }
    }
    battalionMarkerByKey = {};

    for (bk in battalionGroupsMap) {
      if (!Object.prototype.hasOwnProperty.call(battalionGroupsMap, bk)) continue;
      var grp = battalionGroupsMap[bk];
      if (grp.units.length < 2) continue;
      var cen = centroidLatLon(grp.units);
      var rep = grp.units[0];
      var actLbl = summarizeBattalionActivity(grp.units);
      var marker = L.marker(cen, { icon: makeMarkerIcon(grp.side, BATTALION_SYMBOL_SIZE, actLbl) });
      marker.bindTooltip(grp.battalion + ' (battalion, ' + grp.units.length + ' companies)', {
        direction: 'top',
        offset: [0, -22],
      });
      wireBattalionAggregateMarker(marker, rep);
      battalionMarkerByKey[bk] = marker;
    }
    refreshMarkerDisplay();
  }

  function parseUnitStatsCSV(text) {
    var t = text.replace(/^\uFEFF/, '');
    var lines = t.trim().split(/\r?\n/);
    if (!lines.length) return {};
    var header = lines[0].split(',').map(function (h) {
      return h.trim().toLowerCase().replace(/\s+/g, '_');
    });
    var ni = header.indexOf('name');
    var mi = header.indexOf('movement');
    var dfr = header.indexOf('direct_fire_range');
    var dfs = header.indexOf('direct_fire_score');
    var ifr = header.indexOf('indirect_fire_range');
    var ifs = header.indexOf('indirect_fire_score');
    var ccs = header.indexOf('close_combat_score');
    if (ni < 0 || mi < 0 || dfr < 0 || dfs < 0 || ifr < 0 || ifs < 0 || ccs < 0) {
      throw new Error('unitStats.csv must include: name, movement, direct_fire_range, direct_fire_score, indirect_fire_range, indirect_fire_score, close_combat_score');
    }
    var out = {};
    var i;
    for (i = 1; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      var parts = lines[i].split(',');
      var nm = parts[ni] ? parts[ni].trim() : '';
      if (!nm) continue;
      out[nm] = {
        movement: parseFloat(parts[mi]),
        dfRange: parseFloat(parts[dfr]),
        dfScore: parseFloat(parts[dfs]),
        ifRange: parseFloat(parts[ifr]),
        ifScore: parseFloat(parts[ifs]),
        ccScore: parseFloat(parts[ccs]),
      };
    }
    return out;
  }

  /** "4 M60;9 M113" → [{ count: 4, name: "M60" }, …] */
  function parseEquipmentField(s) {
    if (!s || typeof s !== 'string') return [];
    var chunks = s.split(';');
    var out = [];
    var ci;
    for (ci = 0; ci < chunks.length; ci++) {
      var seg = chunks[ci].trim();
      if (!seg) continue;
      var m = /^(\d+)\s+(.+)$/.exec(seg);
      if (!m) continue;
      out.push({ count: parseInt(m[1], 10), name: m[2].trim() });
    }
    return out;
  }

  function formatCombatScoreNumber(n) {
    if (!isFinite(n)) return '0';
    var r = Math.round(n * 100) / 100;
    if (Math.abs(r - Math.round(r)) < 1e-9) return String(Math.round(r));
    return String(r);
  }

  function computeCombatTotalsFromSpecs(specs, statsMap) {
    var df = 0;
    var inf = 0;
    var cc = 0;
    var si;
    for (si = 0; si < specs.length; si++) {
      var p = specs[si];
      var st = statsMap[p.name];
      if (!st) {
        console.warn('Unknown equipment type in unit file:', p.name);
        continue;
      }
      var n = p.count;
      df += n * st.dfScore;
      inf += n * st.ifScore;
      cc += n * st.ccScore;
    }
    return { df: df, inf: inf, cc: cc };
  }

  function buildCombatSidebarHtml(df, inf, cc) {
    return (
      '<div class="unit-combat-left">' +
      '<div><span class="unit-combat-abbr">DF</span> ' +
      escapeHtmlLite(formatCombatScoreNumber(df)) +
      '</div>' +
      '<div><span class="unit-combat-abbr">IF</span> ' +
      escapeHtmlLite(formatCombatScoreNumber(inf)) +
      '</div>' +
      '<div><span class="unit-combat-abbr">CC</span> ' +
      escapeHtmlLite(formatCombatScoreNumber(cc)) +
      '</div>' +
      '</div>'
    );
  }

  function buildEquipmentSidebarHtml(specs) {
    if (!specs.length) {
      return '<div class="unit-equip-right"></div>';
    }
    var lines = [];
    var li;
    for (li = 0; li < specs.length; li++) {
      lines.push(
        escapeHtmlLite(String(specs[li].count) + '\u00d7 ' + specs[li].name),
      );
    }
    return '<div class="unit-equip-right">' + lines.join('<br>') + '</div>';
  }

  function parseCSV(text) {
    var t = text.replace(/^\uFEFF/, '');
    var lines = t.trim().split(/\r?\n/);
    if (!lines.length) return [];
    var header = lines[0].split(',').map(function (h) {
      return h.trim().toLowerCase();
    });
    var ci = header.indexOf('company');
    var bi = header.indexOf('battalion');
    var lati = header.indexOf('lat');
    var loni = header.indexOf('lon') >= 0 ? header.indexOf('lon') : header.indexOf('lng');
    var vi = header.indexOf('vehicle');
    var ei = header.indexOf('equipment');
    if (ci < 0 || bi < 0 || lati < 0 || loni < 0) {
      throw new Error('CSV must include columns: company, battalion, lat, lon (or lng)');
    }
    var rows = [];
    var i;
    for (i = 1; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      var parts = lines[i].split(',');
      var vv = vi >= 0 && parts[vi] ? String(parts[vi]).trim().toLowerCase() : 'tracked';
      if (vv !== 'truck') vv = 'tracked';
      var equipStr = '';
      if (ei >= 0 && parts.length > ei) {
        equipStr = parts.slice(ei).join(',').trim();
      }
      rows.push({
        company: parts[ci] ? parts[ci].trim() : '',
        battalion: parts[bi] ? parts[bi].trim() : '',
        lat: parseFloat(parts[lati]),
        lon: parseFloat(parts[loni]),
        vehicle: vv,
        equipment: equipStr,
      });
    }
    return rows;
  }

  function unitKey(side, u) {
    if (u.key) return u.key;
    return side + '|' + u.company + '|' + u.battalion;
  }

  function battalionKey(side, u) {
    return side + '|' + u.battalion;
  }

  var selectionMode = null;
  var selectionCompanyKey = null;
  var selectionBattalionKey = null;
  /** Umpire magic move: next map right-click teleports selected company; left-click cancels. */
  var magicMovePending = false;

  function getMilsymbolApi() {
    return typeof ms !== 'undefined' ? ms : typeof window.milsymbol !== 'undefined' ? window.milsymbol : null;
  }

  var ACTIVITY_LABEL_ROW_PX = 15;

  function wrapMarkerHtml(innerSymbolHtml, activityText, showRouteClear, spotted, combatLeftHtml, equipRightHtml) {
    var sc = !!showRouteClear;
    var sp = !!spotted;
    var btn = sc
      ? '<button type="button" class="route-clear-unit-btn" title="Clear entire route" aria-label="Clear entire route">\u2715</button>'
      : '';
    var eye = sp
      ? '<span class="unit-spotted-eye" title="Spotted" aria-label="Spotted">' + SPOTTED_EYE_SVG + '</span>'
      : '';
    var hasSides = !!(combatLeftHtml || equipRightHtml);
    var centerBlock =
      '<div class="marker-center-col">' +
      '<div class="marker-symbol-slot">' +
      eye +
      innerSymbolHtml +
      '</div>' +
      '<div class="unit-activity-label">' +
      activityText +
      '</div>' +
      '</div>';
    var bodyHtml;
    if (hasSides) {
      bodyHtml =
        '<div class="marker-company-body">' +
        (combatLeftHtml || '') +
        centerBlock +
        (equipRightHtml || '') +
        '</div>';
    } else {
      bodyHtml = centerBlock;
    }
    return (
      '<div class="marker-root' +
      (sc ? ' marker-root--route-clear' : '') +
      (hasSides ? ' marker-root--with-sidebars' : '') +
      '">' +
      btn +
      bodyHtml +
      '</div>'
    );
  }

  /** APP-6 15-char SIDC: friendly/hostile infantry, plus activity caption under symbol */
  function makeMarkerIcon(side, symbolPixelSize, activityText, showRouteClear, spotted, combatLeftHtml, equipRightHtml) {
    var showClr = !!showRouteClear;
    var sp = !!spotted;
    var caption = activityText || 'halted';
    var szPx = typeof symbolPixelSize === 'number' && !isNaN(symbolPixelSize) ? symbolPixelSize : SYMBOL_SIZE;
    var msApi = getMilsymbolApi();
    var isBlue = side === 'blue';
    var sidc = isBlue ? 'SFGPUCI---**F***' : 'SHGPUCI---**H***';
    var hasSides = !!(combatLeftHtml || equipRightHtml);
    var cw = hasSides ? COMBAT_SIDEBAR_W : 0;
    var ew = hasSides ? EQUIP_SIDEBAR_W : 0;
    if (msApi && msApi.Symbol) {
      try {
        var sym = new msApi.Symbol(sidc, {
          size: szPx,
          frame: true,
          icon: true,
          fill: true,
        });
        var svg = sym.asSVG();
        var sz = sym.getSize ? sym.getSize() : { width: szPx, height: szPx };
        var ac = sym.getAnchor ? sym.getAnchor() : { x: szPx / 2, y: szPx / 2 };
        var html = wrapMarkerHtml(svg, caption, showClr, sp, combatLeftHtml, equipRightHtml);
        var totalW = cw + sz.width + ew;
        var totalH = sz.height + ACTIVITY_LABEL_ROW_PX;
        var ax = cw + ac.x;
        return L.divIcon({
          className: 'nato-symbol-marker marker-with-activity unit-marker unit-marker-' + side,
          html: html,
          iconSize: [totalW, totalH],
          iconAnchor: [ax, ac.y],
        });
      } catch (e) {
        console.warn('milsymbol failed, using fallback', e);
      }
    }
    var fill = isBlue ? '#1e5cb3' : '#b32424';
    var border = isBlue ? '#0d3d82' : '#7a1515';
    var d = Math.max(14, Math.round(szPx * 0.32));
    var dot =
      '<div style="width:' +
      d +
      'px;height:' +
      d +
      'px;border-radius:50%;background:' +
      fill +
      ';border:2px solid ' +
      border +
      ';margin:0 auto;"></div>';
    var html2 = wrapMarkerHtml(dot, caption, showClr, sp, combatLeftHtml, equipRightHtml);
    var symW = d + 4;
    var ax = cw + Math.round(symW / 2);
    var totalW2 = cw + symW + ew;
    var totalH2 = symW + ACTIVITY_LABEL_ROW_PX;
    return L.divIcon({
      className: 'nato-symbol-marker marker-with-activity unit-marker unit-marker-' + side,
      html: html2,
      iconSize: [totalW2, totalH2],
      iconAnchor: [ax, ax],
    });
  }

  function getMergedZOCKeys() {
    var combined = {};
    if (!selectionMode) {
      return combined;
    }
    if (selectionMode === 'company' && selectionCompanyKey) {
      var uCo = null;
      units.forEach(function (u) {
        if (unitKey(u.side, u) === selectionCompanyKey) uCo = u;
      });
      if (uCo) combined = hexesForUnitZOC(uCo);
    } else if (selectionMode === 'battalion' && selectionBattalionKey) {
      units.forEach(function (u) {
        if (battalionKey(u.side, u) === selectionBattalionKey) {
          combined = mergeHexKeySets(combined, hexesForUnitZOC(u));
        }
      });
    }
    return combined;
  }

  function redrawSelectionOverlays() {
    zocLayerGroup.clearLayers();
    losLayerGroup.clearLayers();
    if (!selectionMode) return;
    var key =
      selectionMode === 'company' ? selectionCompanyKey : selectionBattalionKey;
    if (!key) return;
    GameApi.selectionOverlay(selectionMode, key)
      .then(function (data) {
        var zocStyle = {
          color: '#f5cc00',
          weight: 4,
          opacity: 0.95,
          lineCap: 'round',
          lineJoin: 'round',
          interactive: false,
        };
        var losStyle = {
          color: '#2563eb',
          weight: 4,
          opacity: 1,
          dashArray: '12 10',
          lineCap: 'round',
          lineJoin: 'round',
          interactive: false,
          pane: 'losPane',
          renderer: losSvgRenderer,
        };
        var i;
        for (i = 0; i < (data.zocLines || []).length; i++) {
          L.polyline(data.zocLines[i], zocStyle).addTo(zocLayerGroup);
        }
        for (i = 0; i < (data.losLines || []).length; i++) {
          L.polyline(data.losLines[i], losStyle).addTo(losLayerGroup);
        }
      })
      .catch(function (err) {
        console.warn('selection overlay', err);
      });
  }

  function redrawZoneOfControl() {
    redrawSelectionOverlays();
  }

  function redrawLineOfSight() {
    redrawSelectionOverlays();
  }

  function stripMarkerSelectionClasses(mark) {
    var el = mark.getElement();
    if (el) {
      el.classList.remove('unit-marker-selected-battalion', 'unit-marker-selected-company');
    }
  }

  function updateMarkerClasses() {
    Object.keys(markerByKey).forEach(function (k) {
      stripMarkerSelectionClasses(markerByKey[k]);
    });
    Object.keys(battalionMarkerByKey).forEach(function (k) {
      stripMarkerSelectionClasses(battalionMarkerByKey[k]);
    });

    if (selectionMode === 'company' && selectionCompanyKey) {
      var m = markerByKey[selectionCompanyKey];
      var onMapAgg =
        selectionBattalionKey &&
        battalionOverlapAgg[selectionBattalionKey] &&
        battalionMarkerByKey[selectionBattalionKey];

      if (m && map.hasLayer(m)) {
        var node = m.getElement();
        if (node) node.classList.add('unit-marker-selected-company');
      } else if (onMapAgg && map.hasLayer(battalionMarkerByKey[selectionBattalionKey])) {
        var bnEl = battalionMarkerByKey[selectionBattalionKey].getElement();
        if (bnEl) bnEl.classList.add('unit-marker-selected-company');
      }
    } else if (selectionMode === 'battalion' && selectionBattalionKey) {
      if (battalionOverlapAgg[selectionBattalionKey] && battalionMarkerByKey[selectionBattalionKey]) {
        var bm = battalionMarkerByKey[selectionBattalionKey];
        if (map.hasLayer(bm)) {
          var bmEl = bm.getElement();
          if (bmEl) bmEl.classList.add('unit-marker-selected-battalion');
        }
      } else {
        units.forEach(function (u) {
          if (battalionKey(u.side, u) === selectionBattalionKey) {
            var mk = markerByKey[unitKey(u.side, u)];
            if (mk && map.hasLayer(mk)) {
              var el = mk.getElement();
              if (el) el.classList.add('unit-marker-selected-battalion');
            }
          }
        });
      }
    }
    redrawZoneOfControl();
    redrawLineOfSight();
    redrawRouteOverlay();
  }

  function setStatus(text) {
    var el = document.getElementById('selection-status');
    if (el) el.textContent = text;
  }

  function cancelMagicMove() {
    if (!magicMovePending) return;
    magicMovePending = false;
    var c = map.getContainer();
    if (c) c.classList.remove('order-magic-move-active');
  }

  function updateOrderMenusVisibility() {
    var nav = document.getElementById('order-menus');
    if (!nav) return;
    var show = selectionMode === 'company' && !!selectionCompanyKey;
    if (!show) {
      cancelMagicMove();
      nav.hidden = true;
    } else {
      nav.hidden = false;
    }
  }

  function startMagicMove() {
    if (!(selectionMode === 'company' && selectionCompanyKey)) {
      setStatus('Select a company (double-click unit) to use magic move.');
      return;
    }
    if (magicMovePending) {
      cancelMagicMove();
      setStatus('Magic move cancelled.');
      return;
    }
    magicMovePending = true;
    var c = map.getContainer();
    if (c) c.classList.add('order-magic-move-active');
    setStatus('Magic move: right-click map to teleport unit here; left-click map to cancel.');
  }

  function applyMagicTeleport(u, latlng) {
    if (!u || !latlng) return;
    GameApi.magicMove(unitKey(u.side, u), latlng.lat, latlng.lng)
      .then(function (res) {
        mergeServerUnit(res.unit);
        refreshUnitMarkerIcon(u);
        syncBattalionAggregateMarkers();
        redrawRouteOverlay();
        updateMarkerClasses();
        setStatus(u.company + ' teleported (magic move).');
      })
      .catch(function (err) {
        setStatus('Magic move failed: ' + err.message);
      });
  }

  function clearSelection() {
    closeIndirectFireModal();
    selectionMode = null;
    selectionCompanyKey = null;
    selectionBattalionKey = null;
    clearRouteOverlay();
    refreshAllUnitMarkerRouteButtons();
    updateMarkerClasses();
    updateOrderMenusVisibility();
    setStatus('No selection');
  }

  function selectBattalion(u) {
    cancelMagicMove();
    closeIndirectFireModal();
    selectionMode = 'battalion';
    selectionBattalionKey = battalionKey(u.side, u);
    selectionCompanyKey = null;
    refreshAllUnitMarkerRouteButtons();
    updateMarkerClasses();
    updateOrderMenusVisibility();
    setStatus('Battalion: ' + u.battalion + ' (' + u.side + ')');
  }

  function selectCompany(u) {
    cancelMagicMove();
    closeIndirectFireModal();
    selectionMode = 'company';
    selectionCompanyKey = unitKey(u.side, u);
    selectionBattalionKey = battalionKey(u.side, u);
    refreshAllUnitMarkerRouteButtons();
    updateMarkerClasses();
    updateOrderMenusVisibility();
    setStatus('Company: ' + u.company + ' — ' + u.battalion + ' (' + u.side + ')');
  }

  function stopEventFromReachingMap(e) {
    var dom = e.originalEvent || e;
    L.DomEvent.stopPropagation(dom);
  }

  function wireMarker(marker, u) {
    var t = null;
    marker.on('click', function (e) {
      stopEventFromReachingMap(e);
      if (t) clearTimeout(t);
      t = setTimeout(function () {
        t = null;
        selectBattalion(u);
      }, CLICK_DELAY_MS);
    });
    marker.on('dblclick', function (e) {
      stopEventFromReachingMap(e);
      if (t) {
        clearTimeout(t);
        t = null;
      }
      selectCompany(u);
    });
  }

  /** Centroid battalion marker: only battalion-level selection makes sense zoomed out. */
  function wireBattalionAggregateMarker(marker, representativeUnit) {
    var u = representativeUnit;
    var t = null;
    marker.on('click', function (e) {
      stopEventFromReachingMap(e);
      if (t) clearTimeout(t);
      t = setTimeout(function () {
        t = null;
        selectBattalion(u);
      }, CLICK_DELAY_MS);
    });
    marker.on('dblclick', function (e) {
      stopEventFromReachingMap(e);
      if (t) {
        clearTimeout(t);
        t = null;
      }
      selectBattalion(u);
    });
  }

  /** Clicks on route UI bubble to the map and would otherwise run clearSelection(). */
  function isClickOnRouteOrUnitRouteControl(e) {
    var oe = e && e.originalEvent;
    var t = oe && oe.target;
    if (!t || typeof t.closest !== 'function') {
      return false;
    }
    return !!(
      t.closest('.route-wp-remove-btn') ||
      t.closest('.route-clear-unit-btn') ||
      t.closest('.route-via-marker') ||
      t.closest('.route-dest-marker')
    );
  }

  map.on('click', function (e) {
    if (isClickOnRouteOrUnitRouteControl(e)) {
      return;
    }
    if (magicMovePending) {
      cancelMagicMove();
      if (selectionMode === 'company' && selectionCompanyKey) {
        setStatus('Magic move cancelled.');
      }
      return;
    }
    clearSelection();
  });

  map.on('contextmenu', function (e) {
    if (magicMovePending && selectionMode === 'company' && selectionCompanyKey) {
      L.DomEvent.preventDefault(e.originalEvent);
      L.DomEvent.stopPropagation(e);
      if (e.originalEvent) L.DomEvent.stopPropagation(e.originalEvent);

      var tgtM = null;
      var um;
      for (um = 0; um < units.length; um++) {
        if (unitKey(units[um].side, units[um]) === selectionCompanyKey) {
          tgtM = units[um];
          break;
        }
      }
      if (!tgtM) {
        cancelMagicMove();
        return;
      }
      applyMagicTeleport(tgtM, e.latlng);
      cancelMagicMove();
      return;
    }

    if (selectionMode !== 'company' || !selectionCompanyKey) {
      return;
    }
    L.DomEvent.preventDefault(e.originalEvent);
    L.DomEvent.stopPropagation(e);
    if (e.originalEvent) L.DomEvent.stopPropagation(e.originalEvent);

    var tgt = null;
    var ux;
    for (ux = 0; ux < units.length; ux++) {
      if (unitKey(units[ux].side, units[ux]) === selectionCompanyKey) {
        tgt = units[ux];
        break;
      }
    }
    if (!tgt) return;

    GameApi.hexKeyAt(e.latlng.lat, e.latlng.lng)
      .then(function (geo) {
        if (!geo.hasRaster) {
          setStatus('Destination outside mv_cost.tif or nodata.');
          return;
        }
        if (tgt.destinationKey) {
          extendRouteWithNewDestination(tgt, geo.key);
          return;
        }
        assignMoveOrder(tgt, geo.key);
      })
      .catch(function (err) {
        setStatus('Move order failed: ' + err.message);
      });
  });

  function mountUnitsFromServer(serverUnits) {
    serverUnits.forEach(function (u) {
      units.push(u);
      var specs = u.equipmentSpecs || [];
      var leftH = buildCombatSidebarHtml(u.totalDirectFire, u.totalIndirectFire, u.totalCloseCombat);
      var rightH = buildEquipmentSidebarHtml(specs);
      var marker = L.marker([u.lat, u.lon], {
        icon: makeMarkerIcon(u.side, SYMBOL_SIZE, u.activity, false, false, leftH, rightH),
      });
      wireMarker(marker, u);
      marker.bindTooltip(u.company + ' — ' + u.battalion, { direction: 'top', offset: [0, -18] });
      markerByKey[u.key || unitKey(u.side, u)] = marker;
    });
  }

  GameApi.bootstrap()
    .then(function (data) {
      var cfg = data.config || {};
      if (cfg.hexOriginLat != null) hexOriginLat = cfg.hexOriginLat;
      if (cfg.hexOriginLon != null) hexOriginLon = cfg.hexOriginLon;
      unitStatsByName = data.unitStats || {};
      syncMvCostMetaFromBootstrap(data.mvCost);
      if (data.simInstantMs != null) simInstantMs = data.simInstantMs;
      updateTimebarFromApi(data.timebar, data.simInstantMs);
      if (!data.mvCost || !data.mvCost.loaded) {
        var fr = (data.mvCost && data.mvCost.failureReason) || '';
        setStatus(
          'mv_cost.tif not loaded — movement unavailable.' + (fr ? ' (' + fr + ')' : ''),
        );
      }
      mountUnitsFromServer(data.units || []);
      buildBattalionGroupsMap();
      createBattalionAggregateMarkers();
      redrawHexGrid();
      if (units.length > 0) {
        var latLngs = units.map(function (u) {
          return L.latLng(u.lat, u.lon);
        });
        map.fitBounds(L.latLngBounds(latLngs).pad(0.15));
        map.once('moveend zoomend', function () {
          redrawHexGrid();
          refreshMarkerDisplay();
          redrawSelectionOverlays();
        });
      }
      redrawSelectionOverlays();
    })
    .catch(function (err) {
      console.error(err);
      setStatus('Failed to load game: ' + err.message);
      redrawHexGrid();
    });

  var indirectFireMission = {
    target: null,
    candidateGroups: [],
    firingRows: [],
  };

  function getSelectedCompanyUnit() {
    if (!(selectionMode === 'company' && selectionCompanyKey)) return null;
    var ui;
    for (ui = 0; ui < units.length; ui++) {
      if (unitKey(units[ui].side, units[ui]) === selectionCompanyKey) return units[ui];
    }
    return null;
  }

  function buildIndirectFireCandidateGroups(target) {
    var enemySide = target.side === 'blue' ? 'red' : 'blue';
    var groups = [];
    var ui;
    for (ui = 0; ui < units.length; ui++) {
      var u = units[ui];
      if (u.side !== enemySide) continue;
      var specs = u.equipmentSpecs || [];
      var weapons = [];
      var si;
      for (si = 0; si < specs.length; si++) {
        var st = unitStatsByName[specs[si].name];
        if (!st || !(st.ifScore > 0)) continue;
        var dKm = distanceKmLatLon(u.lat, u.lon, target.lat, target.lon);
        if (st.ifRange < dKm) continue;
        weapons.push({
          name: specs[si].name,
          count: specs[si].count,
          ifRange: st.ifRange,
          ifScore: st.ifScore,
          distKm: dKm,
        });
      }
      if (weapons.length) groups.push({ unit: u, weapons: weapons });
    }
    return groups;
  }

  function closeIndirectFireModal() {
    var modal = document.getElementById('indirect-fire-modal');
    if (modal) modal.hidden = true;
    indirectFireMission.target = null;
    indirectFireMission.candidateGroups = [];
    indirectFireMission.firingRows = [];
  }

  function addIndirectFireRowsFromCandidateGroup(groupIndex) {
    var grp = indirectFireMission.candidateGroups[groupIndex];
    if (!grp) return;
    var wi;
    for (wi = 0; wi < grp.weapons.length; wi++) {
      var w = grp.weapons[wi];
      var rowId = unitKey(grp.unit.side, grp.unit) + '|' + w.name;
      var exists = false;
      var ri;
      for (ri = 0; ri < indirectFireMission.firingRows.length; ri++) {
        if (indirectFireMission.firingRows[ri].rowId === rowId) {
          exists = true;
          break;
        }
      }
      if (exists) continue;
      indirectFireMission.firingRows.push({
        rowId: rowId,
        unit: grp.unit,
        weaponName: w.name,
        tubeCount: w.count,
        ifScore: w.ifScore,
        ifRange: w.ifRange,
        distKm: w.distKm,
      });
    }
    renderIndirectFireSelectedRows();
    refreshIndirectFireTotalScore();
  }

  function renderIndirectFireCandidateList() {
    var ul = document.getElementById('if-candidate-list');
    if (!ul) return;
    ul.innerHTML = '';
    var g = indirectFireMission.candidateGroups;
    if (!g.length) {
      var li0 = document.createElement('li');
      li0.className = 'if-candidate-empty';
      li0.style.cssText = 'font-size:13px;color:#64748b;padding:8px;';
      li0.textContent = 'No enemy units with IF score > 0 and weapon IF range ≥ distance to target (km).';
      ul.appendChild(li0);
      return;
    }
    var gi;
    for (gi = 0; gi < g.length; gi++) {
      var grp = g[gi];
      var u = grp.unit;
      var wSummary = grp.weapons
        .map(function (w) {
          return w.count + '\u00d7 ' + w.name;
        })
        .join(', ');
      var li = document.createElement('li');
      li.className = 'if-candidate-item';
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'if-candidate-btn';
      btn.setAttribute('data-candidate-group-index', String(gi));
      var t1 = document.createElement('span');
      t1.className = 'if-candidate-title';
      t1.textContent = u.company + ' — ' + u.battalion;
      var t2 = document.createElement('span');
      t2.className = 'if-candidate-meta';
      t2.textContent = wSummary;
      btn.appendChild(t1);
      btn.appendChild(t2);
      li.appendChild(btn);
      ul.appendChild(li);
    }
  }

  function renderIndirectFireSelectedRows() {
    var wrap = document.getElementById('if-selected-rows');
    if (!wrap) return;
    var rows = indirectFireMission.firingRows;
    if (!rows.length) {
      wrap.innerHTML =
        '<div class="if-selected-placeholder" style="font-size:13px;color:#64748b;padding:8px;">Click a unit on the left to add firing units.</div>';
      return;
    }
    var h = '';
    var ri;
    for (ri = 0; ri < rows.length; ri++) {
      var row = rows[ri];
      var u = row.unit;
      var opts = '';
      var r;
      for (r = 1; r <= 20; r++) {
        opts += '<option value="' + r + '"' + (r === 1 ? ' selected' : '') + '>' + r + '</option>';
      }
      h +=
        '<div class="if-firing-row" data-row-index="' +
        ri +
        '">' +
        '<div class="if-firing-row-title">' +
        escapeHtmlLite(u.company + ' — ' + u.battalion) +
        '</div>' +
        '<div class="if-firing-row-grid">' +
        '<span class="if-firing-row-label">Range to target</span><span>' +
        escapeHtmlLite(row.distKm.toFixed(1) + ' km') +
        '</span>' +
        '<span class="if-firing-row-label">IF weapon</span><span>' +
        escapeHtmlLite(row.weaponName) +
        '</span>' +
        '<span class="if-firing-row-label">IF score (per tube)</span><span>' +
        escapeHtmlLite(formatCombatScoreNumber(row.ifScore)) +
        '</span>' +
        '<span class="if-firing-row-label">Tubes</span><span>' +
        String(row.tubeCount) +
        '</span>' +
        '</div>' +
        '<label class="if-rounds-select">Rounds ' +
        '<select class="if-rounds-select-input" data-row-index="' +
        ri +
        '">' +
        opts +
        '</select></label>' +
        '</div>';
    }
    wrap.innerHTML = h;
  }

  function refreshIndirectFireTotalScore() {
    var el = document.getElementById('if-total-score');
    if (!el) return;
    var wrap = document.getElementById('if-selected-rows');
    var chkP = document.getElementById('if-chk-preplanned');
    var chkD = document.getElementById('if-chk-dug-in');
    var pre = chkP && chkP.checked;
    var dug = chkD && chkD.checked;
    var base = 0;
    var ri;
    for (ri = 0; ri < indirectFireMission.firingRows.length; ri++) {
      var row = indirectFireMission.firingRows[ri];
      var sel = wrap ? wrap.querySelector('select.if-rounds-select-input[data-row-index="' + ri + '"]') : null;
      var rounds = sel ? parseInt(sel.value, 10) : 1;
      if (!isFinite(rounds) || rounds < 1) rounds = 1;
      base += row.tubeCount * row.ifScore * rounds;
    }
    var mul = (pre ? 2 : 1) * (dug ? 0.5 : 1);
    var total = base * mul;
    el.textContent = formatCombatScoreNumber(total);
  }

  function openIndirectFireModal() {
    if (!(selectionMode === 'company' && selectionCompanyKey)) {
      setStatus('Select a company (double-click) to plan indirect fire.');
      return;
    }
    var target = getSelectedCompanyUnit();
    if (!target) {
      setStatus('No selected company unit.');
      return;
    }
    cancelMagicMove();
    indirectFireMission.target = target;
    indirectFireMission.firingRows = [];
    var modal = document.getElementById('indirect-fire-modal');
    var line = document.getElementById('if-modal-target-line');
    var chkP = document.getElementById('if-chk-preplanned');
    var chkD = document.getElementById('if-chk-dug-in');
    if (chkP) chkP.checked = false;
    if (chkD) chkD.checked = false;
    if (line) {
      line.textContent =
        'Target: ' + target.company + ' — ' + target.battalion + ' (' + target.side + ')';
    }
    GameApi.indirectFireCandidates(selectionCompanyKey)
      .then(function (data) {
        indirectFireMission.candidateGroups = (data.groups || []).map(function (g) {
          return {
            unit: getUnitByKey(g.unitKey),
            weapons: (g.weapons || []).map(function (w) {
              return {
                name: w.name,
                count: w.count,
                ifRange: w.ifRange,
                ifScore: w.ifScore,
                distKm: w.distKm,
              };
            }),
          };
        });
        renderIndirectFireCandidateList();
        renderIndirectFireSelectedRows();
        refreshIndirectFireTotalScore();
        if (modal) modal.hidden = false;
      })
      .catch(function (err) {
        setStatus('Indirect fire setup failed: ' + err.message);
      });
  }

  function executeIndirectFireMission() {
    if (!indirectFireMission.target) {
      closeIndirectFireModal();
      return;
    }
    if (!indirectFireMission.firingRows.length) {
      setStatus('Indirect fire: add at least one firing unit.');
      return;
    }
    var wrap = document.getElementById('if-selected-rows');
    var chkP = document.getElementById('if-chk-preplanned');
    var chkD = document.getElementById('if-chk-dug-in');
    var pre = chkP && chkP.checked;
    var dug = chkD && chkD.checked;
    var firingRows = [];
    var ri;
    for (ri = 0; ri < indirectFireMission.firingRows.length; ri++) {
      var row = indirectFireMission.firingRows[ri];
      var selR = wrap ? wrap.querySelector('select.if-rounds-select-input[data-row-index="' + ri + '"]') : null;
      var rounds = selR ? parseInt(selR.value, 10) : 1;
      firingRows.push({
        unit_key: unitKey(row.unit.side, row.unit),
        weapon_name: row.weaponName,
        tube_count: row.tubeCount,
        if_score: row.ifScore,
        rounds: rounds,
      });
    }
    GameApi.indirectFireResolve({
      target_key: unitKey(indirectFireMission.target.side, indirectFireMission.target),
      firing_rows: firingRows,
      preplanned: pre,
      dug_in: dug,
    })
      .then(function (res) {
        setStatus(res.message || 'Indirect fire executed.');
        closeIndirectFireModal();
      })
      .catch(function (err) {
        setStatus('Indirect fire failed: ' + err.message);
      });
  }

  function initIndirectFireModal() {
    var modal = document.getElementById('indirect-fire-modal');
    if (!modal || modal.getAttribute('data-if-init') === '1') return;
    modal.setAttribute('data-if-init', '1');
    var btnClose = document.getElementById('if-modal-close');
    var btnFire = document.getElementById('if-btn-fire');
    var chkP = document.getElementById('if-chk-preplanned');
    var chkD = document.getElementById('if-chk-dug-in');
    var list = document.getElementById('if-candidate-list');
    var selectedWrap = document.getElementById('if-selected-rows');
    if (btnClose) {
      btnClose.addEventListener('click', function () {
        setStatus('Indirect fire mission cancelled.');
        closeIndirectFireModal();
      });
    }
    if (btnFire) {
      btnFire.addEventListener('click', function () {
        executeIndirectFireMission();
      });
    }
    if (chkP) chkP.addEventListener('change', refreshIndirectFireTotalScore);
    if (chkD) chkD.addEventListener('change', refreshIndirectFireTotalScore);
    if (list) {
      list.addEventListener('click', function (ev) {
        var btn = ev.target && ev.target.closest ? ev.target.closest('.if-candidate-btn') : null;
        if (!btn) return;
        var idx = parseInt(btn.getAttribute('data-candidate-group-index'), 10);
        if (isNaN(idx)) return;
        addIndirectFireRowsFromCandidateGroup(idx);
      });
    }
    if (selectedWrap) {
      selectedWrap.addEventListener('change', function (ev) {
        if (ev.target && ev.target.classList && ev.target.classList.contains('if-rounds-select-input')) {
          refreshIndirectFireTotalScore();
        }
      });
    }
  }

  function initOrderMenus() {
    var nav = document.getElementById('order-menus');
    if (!nav) return;
    nav.addEventListener('mousedown', function (ev) {
      if (ev.stopPropagation) ev.stopPropagation();
    });
    nav.addEventListener('click', function (ev) {
      var t = ev.target;
      if (!t || typeof t.getAttribute !== 'function') return;
      var act = t.getAttribute('data-action');
      if (!act) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (act === 'umpire-magic-move') {
        startMagicMove();
        return;
      }
      if (act === 'enemy-indirect-fire') {
        openIndirectFireModal();
        return;
      }
      if (act === 'enemy-direct-fire') {
        setStatus('Enemy order — direct fire (not implemented).');
        return;
      }
      if (act === 'enemy-assault') {
        setStatus('Enemy order — assault (not implemented).');
        return;
      }
      if (act === 'friendly-move-traveling') {
        setStatus('Friendly order — move — traveling (not implemented).');
        return;
      }
      if (act === 'friendly-move-traveling-overwatch') {
        setStatus('Friendly order — move — traveling overwatch (not implemented).');
        return;
      }
      if (act === 'friendly-move-bounding-overwatch') {
        setStatus('Friendly order — move — bounding overwatch (not implemented).');
        return;
      }
    });
  }

  initIndirectFireModal();
  initOrderMenus();
  initAcquisitionModalDrag();

  map.on('zoomend', refreshMarkerDisplay);
})();
