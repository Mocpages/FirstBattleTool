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
      return jsonFetch('/api/units/' + enc(unitKey) + '/info');
    },
    moveOrder: function (unitKey, goalKey, extend) {
      return jsonFetch('/api/units/' + enc(unitKey) + '/move-order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal_key: goalKey, extend: !!extend }),
      });
    },
    clearRoute: function (unitKey) {
      return jsonFetch('/api/units/' + enc(unitKey) + '/clear-route', { method: 'POST' });
    },
    magicMove: function (unitKey, lat, lon) {
      return jsonFetch('/api/units/' + enc(unitKey) + '/magic-move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat: lat, lon: lon }),
      });
    },
    removeWaypoint: function (unitKey, index) {
      return jsonFetch('/api/units/' + enc(unitKey) + '/remove-waypoint', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: index }),
      });
    },
    waypoint: function (unitKey, kind, viaIndex, lat, lon) {
      return jsonFetch('/api/units/' + enc(unitKey) + '/waypoint', {
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
    indirectFireCandidates: function (targetKey) {
      return jsonFetch('/api/indirect-fire/candidates?target_key=' + enc(targetKey));
    },
    indirectFireResolve: function (body) {
      return jsonFetch('/api/indirect-fire/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    },
  };

  global.GameApi = GameApi;
})(typeof window !== 'undefined' ? window : globalThis);
