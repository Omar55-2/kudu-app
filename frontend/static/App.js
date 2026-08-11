/* Kudu Ticketing - front-end behavior */

const _pickerState = {};

function _escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function initAssigneePicker(prefix, employees) {
  const searchInput = document.getElementById(prefix + "_search");
  const valueInput = document.getElementById(prefix + "_value");
  const resultsContainer = document.getElementById(prefix + "_results");
  const autoAssignBtn = document.getElementById(prefix + "_auto_assign_btn");

  if (!searchInput || !valueInput || !resultsContainer) return;

  _pickerState[prefix] = { all: employees || [], active: employees || [] };

  function render(list) {
    if (!list || list.length === 0) {
      resultsContainer.innerHTML = '<div class="picker-empty">No matching employees found</div>';
      resultsContainer.style.display = "block";
      return;
    }

    resultsContainer.innerHTML = list
      .map(function (e) {
        return (
          '<div class="picker-item" data-id="' + e.id + '" data-name="' + _escapeHtml(e.name) + '">' +
            '<div>' +
              '<strong>' + _escapeHtml(e.name) + '</strong> ' +
              '<small style="color:#6b7290;">(' + _escapeHtml(e.email) + ')</small>' +
            '</div>' +
            '<span class="picker-meta">' + _escapeHtml(e.department) + ' &middot; ' + e.open_count + ' open</span>' +
          '</div>'
        );
      })
      .join("");

    resultsContainer.style.display = "block";

    resultsContainer.querySelectorAll(".picker-item").forEach(function (el) {
      el.addEventListener("click", function () {
        valueInput.value = el.getAttribute("data-id");
        searchInput.value = el.getAttribute("data-name");
        resultsContainer.style.display = "none";
      });
    });
  }

  searchInput.addEventListener("input", function () {
    const query = searchInput.value.trim().toLowerCase();
    const pool = _pickerState[prefix].active;

    if (!query) {
      valueInput.value = "";
      resultsContainer.style.display = "none";
      return;
    }

    const filtered = pool.filter(function (e) {
      const nameMatch = e.name && e.name.toLowerCase().includes(query);
      const emailMatch = e.email && e.email.toLowerCase().includes(query);
      return nameMatch || emailMatch;
    });

    render(filtered);
  });

  searchInput.addEventListener("focus", function () {
    if (!searchInput.value.trim()) {
      render(_pickerState[prefix].active);
    }
  });

  if (autoAssignBtn) {
    autoAssignBtn.addEventListener("click", function (e) {
      e.preventDefault();
      const fullPool = _pickerState[prefix].active;
      const pool = fullPool.filter(function (e) {
        return window.CURRENT_USER_ID == null || e.id !== window.CURRENT_USER_ID;
      });

      if (!pool || pool.length === 0) {
        resultsContainer.innerHTML = '<div class="picker-empty">No other employee available to auto-assign in this selection</div>';
        resultsContainer.style.display = "block";
        return;
      }

      const leastBusy = pool.reduce(function (prev, current) {
        return prev.open_count < current.open_count ? prev : current;
      });

      valueInput.value = leastBusy.id;
      searchInput.value = leastBusy.name + " (Auto-assigned: " + leastBusy.open_count + " open)";
      resultsContainer.style.display = "none";
    });
  }

  document.addEventListener("click", function (e) {
    if (!searchInput.contains(e.target) && !resultsContainer.contains(e.target)) {
      resultsContainer.style.display = "none";
    }
  });
}

function filterPickerByDepartment(prefix, department) {
  const state = _pickerState[prefix];
  if (!state) return;

  state.active = department
    ? state.all.filter(function (e) { return e.department === department; })
    : state.all;

  const searchInput = document.getElementById(prefix + "_search");
  const valueInput = document.getElementById(prefix + "_value");
  const resultsContainer = document.getElementById(prefix + "_results");
  if (searchInput) searchInput.value = "";
  if (valueInput) valueInput.value = "";
  if (resultsContainer) resultsContainer.style.display = "none";
}

/* --- Notifications popup --- */
function toggleNotifDropdown(event) {
  event.stopPropagation();
  const dropdown = document.getElementById("notif-dropdown");
  if (dropdown) dropdown.classList.toggle("open");
}

document.addEventListener("click", function (e) {
  const dropdown = document.getElementById("notif-dropdown");
  const bell = document.getElementById("notif-bell");
  if (dropdown && bell && !dropdown.contains(e.target) && !bell.contains(e.target)) {
    dropdown.classList.remove("open");
  }
});

/* --- Table / Board view switch --- */
function setTicketView(view, scopeKey) {
  const table = document.getElementById("ticket-table-view");
  const board = document.getElementById("ticket-board-view");
  const btnTable = document.getElementById("view-btn-table");
  const btnBoard = document.getElementById("view-btn-board");

  if (table) table.style.display = view === "table" ? "" : "none";
  if (board) board.style.display = view === "board" ? "" : "none";
  if (btnTable) btnTable.classList.toggle("active", view === "table");
  if (btnBoard) btnBoard.classList.toggle("active", view === "board");

  try { sessionStorage.setItem("kudu_ticket_view_" + scopeKey, view); } catch (e) { /* ignore */ }
}

function initTicketViewSwitch(scopeKey, defaultView) {
  let saved = defaultView || "table";
  try {
    const stored = sessionStorage.getItem("kudu_ticket_view_" + scopeKey);
    if (stored) saved = stored;
  } catch (e) { /* ignore */ }
  setTicketView(saved, scopeKey);
}

/* --- Smart Assignment (Team feature) --- */
function initSmartAssign(btnId, resultsId, deptSelectId, valueInputId, searchInputId, excludeUserId) {
  const btn = document.getElementById(btnId);
  if (!btn) return;

  btn.addEventListener("click", function (e) {
    e.preventDefault();
    const dept = deptSelectId && document.getElementById(deptSelectId)
      ? document.getElementById(deptSelectId).value
      : "";
    const params = new URLSearchParams();
    if (dept) params.set("department", dept);
    if (excludeUserId) params.set("exclude_user_id", excludeUserId);

    const results = document.getElementById(resultsId);
    results.classList.remove("hidden");
    results.innerHTML = '<div class="text-[12px] text-gray-400">Finding best assignees...</div>';

    fetch("/team/smart-assign?" + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (candidates) {
        if (!candidates.length) {
          results.innerHTML = '<div class="text-[12px] text-gray-400">No eligible candidates found.</div>';
          return;
        }
        const medals = ["🥇", "🥈", "🥉"];
        results.innerHTML = candidates.map(function (c, i) {
          const medal = medals[i] || "•";
          const slaText = c.sla_compliance != null ? (c.sla_compliance + "% SLA") : "";
          return (
            '<div class="flex items-center justify-between gap-2 bg-gray-50 rounded-lg px-3 py-2">' +
              '<div>' +
                '<span class="text-[13px]">' + medal + ' <strong>' + _escapeHtml(c.name) + '</strong></span>' +
                '<div class="text-[11px] text-gray-500">' + c.workload_pct + '% workload &middot; ' + _escapeHtml(c.workload_state) +
                (slaText ? ' &middot; ' + slaText : '') + '</div>' +
              '</div>' +
              '<button type="button" class="text-[11px] font-semibold text-[#ff6337] hover:underline smart-assign-pick" ' +
                'data-id="' + c.id + '" data-name="' + _escapeHtml(c.name) + '">Assign</button>' +
            '</div>'
          );
        }).join("");

        results.querySelectorAll(".smart-assign-pick").forEach(function (el) {
          el.addEventListener("click", function () {
            document.getElementById(valueInputId).value = el.getAttribute("data-id");
            document.getElementById(searchInputId).value = el.getAttribute("data-name");
          });
        });
      })
      .catch(function () {
        results.innerHTML = '<div class="text-[12px] text-red-500">Could not load recommendations.</div>';
      });
  });
}