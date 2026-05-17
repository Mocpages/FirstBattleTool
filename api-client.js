/**
 * HTTP client for the Python simulation backend.
 */
(function (global) {
  'use strict';

  var API_BASE = '';

  function apiUrl(path) {
    return API_BASE + path;
  }

  function jsonFetch(url, options) {
    return fetch(apiUrl(url), options).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (t) {
          throw new Error(t || res.statusText);
        });
      }
      return res.json();
    });
  }

  function enc(key) {
    return encodeURIComponent(key);
  }

  var GameApi = {
    bootstrap: function () {
      return jsonFetch('/api/bootstrap');
    },
    state: function () {
      return jsonFetch('/api/state');
    },
    simTick: function (opts) {
      return jsonFetch('/api/sim/tick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(opts || {}),
      });
    },
    unitInfo: function (unitKey) {
      return jsonFetch('/api/units/info?unit_key=' + enc(unitKey));
    },
    moveOrder: function (unitKey, goalKey, extend) {
      return jsonFetch('/api/units/move-order?unit_key=' + enc(unitKey), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal_key: goalKey, extend: !!extend }),
      });
    },
    clearRoute: function (unitKey) {
      return jsonFetch('/api/units/clear-route?unit_key=' + enc(unitKey), {
        method: 'POST',
      });
    },
    magicMove: function (unitKey, lat, lon) {
      return jsonFetch('/api/units/magic-move?unit_key=' + enc(unitKey), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat: lat, lon: lon }),
      });
    },
    removeWaypoint: function (unitKey, index) {
      return jsonFetch('/api/units/remove-waypoint?unit_key=' + enc(unitKey), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: index }),
      });
    },
    waypoint: function (unitKey, kind, viaIndex, lat, lon) {
      return jsonFetch('/api/units/waypoint?unit_key=' + enc(unitKey), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: kind,
          via_index: viaIndex,
          lat: lat,
          lon: lon,
        }),
      });
    },
    selectionOverlay: function (mode, key) {
      return jsonFetch(
        '/api/selection/overlay?mode=' + encodeURIComponent(mode) + '&key=' + enc(key),
      );
    },
    segmentCosts: function (path) {
      return jsonFetch('/api/route/segment-costs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path }),
      });
    },
    hexKeyAt: function (lat, lon) {
      return jsonFetch('/api/geo/hex-key?lat=' + lat + '&lon=' + lon);
    },
    terrainHexes: function (west, south, east, north) {
      return jsonFetch(
        '/api/terrain/hexes?west=' +
          west +
          '&south=' +
          south +
          '&east=' +
          east +
          '&north=' +
          north,
      );
    },
    acquisitionResolve: function (payload) {
      return jsonFetch('/api/acquisition/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          spotter_key: payload.spotterKey,
          target_key: payload.targetKey,
          entered_hex_key: payload.enteredHexKey,
          from_hex_key: payload.fromHexKey,
          spot_kind: payload.spotKind,
          confirm: payload.confirm,
        }),
      });
    },
    directFireCandidates: function (victimKey) {
      return jsonFetch('/api/direct-fire/candidates?victim_key=' + enc(victimKey));
    },
    directFireOpportunityResolve: function (body) {
      return jsonFetch('/api/direct-fire/opportunity/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          shooter_key: body.shooterKey,
          victim_key: body.victimKey,
          entered_hex_key: body.enteredHexKey,
          from_hex_key: body.fromHexKey || '',
          confirm: !!body.confirm,
        }),
      });
    },
    directFireResolve: function (body) {
      return jsonFetch('/api/direct-fire/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          shooter_keys: body.shooter_keys || [],
          victim_key: body.victim_key,
          victim_response: body.victim_response,
          target_dug_in: body.target_dug_in,
          target_halted_obstacles: body.target_halted_obstacles,
          target_flank_shot: body.target_flank_shot,
          return_dug_in: body.return_dug_in,
          return_halted_obstacles: body.return_halted_obstacles,
          return_flank_shot: body.return_flank_shot,
        }),
      });
    },
    directFireCancel: function () {
      return jsonFetch('/api/direct-fire/cancel', { method: 'POST' });
    },
    indirectFireCandidates: function (targetKey) {
      return jsonFetch('/api/indirect-fire/candidates?target_key=' + enc(targetKey));
    },
    indirectFireTimeToFire: function (firingRows) {
      return jsonFetch('/api/indirect-fire/time-to-fire', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ firing_rows: firingRows }),
      });
    },
    indirectFireResolve: function (body) {
      return jsonFetch('/api/indirect-fire/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    },
    ifMissionPlan: function (body) {
      return jsonFetch('/api/indirect-fire/mission/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    },
    reportsPop: function () {
      return jsonFetch('/api/reports/pop', { method: 'POST' });
    },
    parseCoordinate: function (text) {
      return jsonFetch('/api/geo/parse-coordinate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text }),
      });
    },
    battalionMoveOrder: function (body) {
      return jsonFetch('/api/battalion/move-order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          battalion_key: body.battalionKey,
          route_hex_keys: body.routeHexKeys || [],
          route_texts: body.routeTexts || [],
          movement_type: body.movementType || 'administrative',
          destination_action: body.destinationAction || 'assembly',
          defense_line_hex_keys: body.defenseLineHexKeys || [],
          defense_line_texts: body.defenseLineTexts || [],
          threat_bearing_deg: body.threatBearingDeg,
        }),
      });
    },
  };

  global.GameApi = GameApi;
})(typeof window !== 'undefined' ? window : globalThis);
