/* Live dispatch feed.
 * All server-supplied text is written with textContent, never innerHTML, so
 * nothing in an event (driver notes, model output) can inject markup or script. */
(function () {
  "use strict";

  var feed = document.getElementById("live-feed");
  var statusEl = document.getElementById("sse-status");
  if (!feed || !statusEl || !window.EventSource) return;

  var MAX_ITEMS = 30;

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = "badge " + cls;
  }

  function prependEvent(title, body) {
    var empty = document.getElementById("feed-empty");
    if (empty) empty.remove();

    var li = document.createElement("li");
    li.className = "item";
    var header = document.createElement("div");
    header.className = "item-header";
    var strong = document.createElement("strong");
    strong.textContent = title;
    header.appendChild(strong);
    li.appendChild(header);
    var p = document.createElement("p");
    p.textContent = body;
    li.appendChild(p);

    feed.prepend(li);
    while (feed.children.length > MAX_ITEMS) feed.removeChild(feed.lastChild);
  }

  function parse(e) {
    try {
      var msg = JSON.parse(e.data);
      return msg && msg.data ? msg : null;
    } catch (err) {
      return null;
    }
  }

  var source = new EventSource(feed.getAttribute("data-stream-url"));

  source.addEventListener("connected", function () {
    setStatus("Live", "badge-normal");
  });

  source.addEventListener("disruption_created", function (e) {
    var msg = parse(e);
    if (!msg) return;
    prependEvent(
      "NEW DISRUPTION · " + msg.data.route_code + " (" + msg.timestamp + ")",
      msg.data.severity + " severity, route " + msg.data.route_status +
        ". Review the drafted alerts before publishing."
    );
  });

  source.addEventListener("alert_published", function (e) {
    var msg = parse(e);
    if (!msg) return;
    prependEvent(
      "PUBLISHED " + String(msg.data.channel).toUpperCase() + " · " +
        msg.data.route_code + " (" + msg.timestamp + ")",
      msg.data.message
    );
  });

  source.addEventListener("disruption_resolved", function (e) {
    var msg = parse(e);
    if (!msg) return;
    prependEvent(
      "RESOLVED · " + msg.data.route_code + " (" + msg.timestamp + ")",
      "Route status is now " + msg.data.route_status + "."
    );
  });

  // The server could not replay everything we missed: reload to get current state.
  source.addEventListener("resync", function () {
    window.location.reload();
  });

  source.onerror = function () {
    if (source.readyState === EventSource.CLOSED) {
      setStatus("Disconnected — reload the page", "badge-suspended");
    } else {
      setStatus("Reconnecting...", "badge-delayed");
    }
  };
})();
