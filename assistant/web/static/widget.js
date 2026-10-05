/**
 * NetHız destek asistanı — embeddable chat widget.
 *
 * Plain vanilla JS, no build step, no CDN. Talks to exactly the endpoints documented in
 * docs/contracts.md §4.2, always via relative URLs (never a hardcoded host):
 *
 *   POST /api/login                                  {customer_no}
 *   POST /api/chat                                    {conversation_id, message}
 *   GET  /api/chat/stream?conversation_id=&message=    SSE token stream (falls back to
 *                                                       POST /api/chat on any error)
 *   POST /api/approvals/{approval_id}                  {decision: "granted"|"denied"}
 *   GET  /api/conversations/{conversation_id}/audit
 *
 * SSE contract this client expects from /api/chat/stream (documented here because the
 * HTTP contract only says "SSE token stream of the same turn"):
 *   event: token   data: <plain text chunk, appended to the running reply>
 *   event: final   data: <JSON, same shape as the POST /api/chat response body>
 *   event: error   data: <anything>                    -> client falls back to POST /api/chat
 * If the browser has no EventSource, or the stream errors before a "final" event arrives,
 * the client transparently retries the same turn with POST /api/chat.
 */
(function () {
  "use strict";

  var MODE_LABELS = {
    ROUTER: "Yönlendiriliyor",
    ADVISORY: "Danışma",
    DIAGNOSTIC: "Teşhis",
    STATUS_QUERY: "Durum",
    ACTION: "İşlem",
    AWAITING_APPROVAL: "Onay bekleniyor",
    ESCALATED: "Aktarıldı",
    CLOSING: "Tamamlandı"
  };

  function formatTime(date) {
    try {
      return date.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return "";
    }
  }

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  // ------------------------------------------------------------------------------
  // Login page
  // ------------------------------------------------------------------------------
  function initLoginPage() {
    var form = document.getElementById("login-form");
    if (!form) return;

    var input = document.getElementById("customer_no");
    var errorBox = document.getElementById("login-error");

    function showError(message) {
      if (!errorBox) return;
      errorBox.textContent = message;
      errorBox.hidden = false;
    }

    function hideError() {
      if (!errorBox) return;
      errorBox.hidden = true;
      errorBox.textContent = "";
    }

    function submitLogin(customerNo) {
      hideError();
      return fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ customer_no: customerNo })
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("login_failed");
          }
          window.location.href = "/";
        })
        .catch(function () {
          showError(
            "Giriş yapılamadı. Müşteri numarasını kontrol edip tekrar deneyin (ör. NH-100042)."
          );
        });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var value = (input && input.value || "").trim().toUpperCase();
      if (!value) {
        showError("Lütfen bir müşteri numarası girin.");
        return;
      }
      submitLogin(value);
    });

    var exampleButtons = document.querySelectorAll(".nh-login__example-btn");
    for (var i = 0; i < exampleButtons.length; i++) {
      exampleButtons[i].addEventListener("click", function () {
        var customerNo = this.getAttribute("data-customer-no");
        if (input) input.value = customerNo;
        submitLogin(customerNo);
      });
    }
  }

  // ------------------------------------------------------------------------------
  // Chat widget
  // ------------------------------------------------------------------------------
  function initChatWidget() {
    var root = document.getElementById("chat-widget");
    if (!root) return;

    var launcher = document.getElementById("chat-launcher");
    var launcherBadge = document.getElementById("chat-launcher-badge");
    var panel = document.getElementById("chat-panel");
    var minimizeBtn = document.getElementById("chat-minimize");
    var messagesEl = document.getElementById("chat-messages");
    var typingEl = document.getElementById("typing-indicator");
    var errorEl = document.getElementById("chat-error");
    var form = document.getElementById("chat-form");
    var inputEl = document.getElementById("chat-input");
    var modeBadge = document.getElementById("mode-badge");
    var connectionStatus = document.getElementById("connection-status");
    var auditPanel = document.getElementById("audit-panel");
    var auditSteps = document.getElementById("audit-steps");
    var tplApproval = document.getElementById("tpl-approval-card");
    var tplTicket = document.getElementById("tpl-ticket-chip");

    var state = {
      conversationId: null,
      open: false,
      auditLoaded: false,
      unreadCount: 0
    };

    function scrollToBottom() {
      if (!messagesEl) return;
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function setMode(mode) {
      if (!modeBadge || !mode) return;
      modeBadge.setAttribute("data-mode", mode);
      modeBadge.textContent = MODE_LABELS[mode] || mode;
    }

    function setTyping(isTyping) {
      if (!typingEl) return;
      typingEl.hidden = !isTyping;
      if (isTyping) scrollToBottom();
    }

    function showError(message) {
      if (!errorEl) return;
      errorEl.textContent = message;
      errorEl.hidden = false;
    }

    function clearError() {
      if (!errorEl) return;
      errorEl.hidden = true;
      errorEl.textContent = "";
    }

    function addMessage(role, text) {
      var li = document.createElement("li");
      li.className = "nh-msg nh-msg--" + role;
      li.setAttribute("data-role", role);

      var bubble = document.createElement("div");
      bubble.className = "nh-msg__bubble";
      bubble.textContent = text;
      li.appendChild(bubble);

      var time = document.createElement("time");
      time.className = "nh-msg__time";
      time.textContent = formatTime(new Date());
      li.appendChild(time);

      if (messagesEl) messagesEl.appendChild(li);
      scrollToBottom();

      if (!state.open && role === "assistant") {
        state.unreadCount += 1;
        updateUnreadBadge();
      }
      return { li: li, bubble: bubble };
    }

    function updateUnreadBadge() {
      if (!launcherBadge) return;
      if (state.unreadCount > 0) {
        launcherBadge.hidden = false;
        launcherBadge.textContent = String(state.unreadCount);
      } else {
        launcherBadge.hidden = true;
      }
    }

    function addTicketChip(ticketKey, department) {
      if (!tplTicket || !messagesEl) return;
      var node = tplTicket.content.cloneNode(true);
      var keyEl = node.querySelector('[data-field="ticket_key"]');
      var deptEl = node.querySelector('[data-field="department"]');
      if (keyEl) keyEl.textContent = ticketKey;
      if (deptEl) deptEl.textContent = department ? "(" + department + ")" : "";
      var li = document.createElement("li");
      li.className = "nh-msg nh-msg--system";
      li.appendChild(node);
      messagesEl.appendChild(li);
      scrollToBottom();
    }

    function addApprovalCard(approvalId, promptText) {
      if (!tplApproval || !messagesEl) return;
      var fragment = tplApproval.content.cloneNode(true);
      var card = fragment.querySelector(".nh-approval-card");
      var textEl = fragment.querySelector('[data-field="prompt"]');
      if (textEl) textEl.textContent = promptText || "Bu işlemi onaylıyor musunuz?";
      if (card) card.setAttribute("data-approval-id", approvalId);

      var buttons = fragment.querySelectorAll("[data-decision]");
      for (var i = 0; i < buttons.length; i++) {
        buttons[i].addEventListener("click", function () {
          handleApprovalDecision(approvalId, this.getAttribute("data-decision"), card, buttons);
        });
      }

      messagesEl.appendChild(fragment);
      scrollToBottom();
    }

    function handleApprovalDecision(approvalId, decision, card, buttons) {
      for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
      fetch("/api/approvals/" + encodeURIComponent(approvalId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ decision: decision })
      })
        .then(function (response) {
          return response.json().catch(function () { return {}; });
        })
        .then(function (data) {
          var status = document.createElement("p");
          status.className = "nh-approval-card__result";
          status.textContent =
            decision === "granted" ? "Onaylandı." : "Reddedildi.";
          if (card) card.appendChild(status);
          if (data && data.mode) setMode(data.mode);
          if (data && data.reply_tr) addMessage("assistant", data.reply_tr);
          if (data && data.ticket_key) addTicketChip(data.ticket_key, data.department);
        })
        .catch(function () {
          var status = document.createElement("p");
          status.className = "nh-approval-card__result nh-approval-card__result--error";
          status.textContent = "İsteğiniz gönderilemedi, lütfen tekrar deneyin.";
          if (card) card.appendChild(status);
          for (var i = 0; i < buttons.length; i++) buttons[i].disabled = false;
        });
    }

    function renderActions(actions) {
      if (!actions || !actions.length || !messagesEl) return;
      var li = document.createElement("li");
      li.className = "nh-msg nh-msg--system";
      var box = document.createElement("div");
      box.className = "nh-actions-box";
      box.setAttribute("data-testid", "actions-box");
      for (var i = 0; i < actions.length; i++) {
        var action = actions[i];
        var chip = document.createElement("span");
        chip.className = "nh-actions-box__chip";
        var label =
          (action && (action.label_tr || action.name || action.type)) ||
          (typeof action === "string" ? action : "işlem");
        chip.textContent = String(label);
        box.appendChild(chip);
      }
      li.appendChild(box);
      messagesEl.appendChild(li);
      scrollToBottom();
    }

    function applyTurnResult(data) {
      if (!data) return;
      state.conversationId = data.conversation_id || state.conversationId;
      if (data.mode) setMode(data.mode);
      if (data.reply_tr) addMessage("assistant", data.reply_tr);
      renderActions(data.actions);
      if (data.ticket_key) addTicketChip(data.ticket_key, data.department);
      if (data.requires_approval && data.approval_id) {
        addApprovalCard(data.approval_id, data.reply_tr);
      }
    }

    function sendViaPost(message) {
      return fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          conversation_id: state.conversationId,
          message: message
        })
      })
        .then(function (response) {
          if (!response.ok) throw new Error("chat_failed");
          return response.json();
        })
        .then(function (data) {
          setTyping(false);
          applyTurnResult(data);
        })
        .catch(function () {
          setTyping(false);
          showError(
            "Şu anda asistana ulaşılamıyor. Lütfen birazdan tekrar deneyin."
          );
        });
    }

    function sendViaStream(message) {
      if (typeof window.EventSource === "undefined") {
        return sendViaPost(message);
      }

      var params = new URLSearchParams({
        conversation_id: state.conversationId || "",
        message: message
      });
      var source;
      var streamed = null;
      var finished = false;

      try {
        source = new EventSource("/api/chat/stream?" + params.toString());
      } catch (e) {
        return sendViaPost(message);
      }

      source.addEventListener("token", function (event) {
        clearError();
        if (streamed === null) {
          setTyping(false);
          streamed = addMessage("assistant", "");
        }
        streamed.bubble.textContent += event.data;
        scrollToBottom();
      });

      source.addEventListener("final", function (event) {
        finished = true;
        source.close();
        setTyping(false);
        var data;
        try {
          data = JSON.parse(event.data);
        } catch (e) {
          data = null;
        }
        if (!data) {
          showError("Şu anda asistana ulaşılamıyor. Lütfen birazdan tekrar deneyin.");
          return;
        }
        state.conversationId = data.conversation_id || state.conversationId;
        if (data.mode) setMode(data.mode);
        if (streamed && data.reply_tr) {
          // the streamed text IS the reply; avoid rendering it twice.
          streamed.bubble.textContent = data.reply_tr;
        } else if (data.reply_tr) {
          addMessage("assistant", data.reply_tr);
        }
        renderActions(data.actions);
        if (data.ticket_key) addTicketChip(data.ticket_key, data.department);
        if (data.requires_approval && data.approval_id) {
          addApprovalCard(data.approval_id, data.reply_tr);
        }
      });

      source.addEventListener("error", function () {
        source.close();
        if (!finished) {
          sendViaPost(message);
        }
      });

      // Network-level errors on an already-open stream also land here if the server never
      // sends a well-formed "error" event — belt and suspenders.
      source.onerror = function () {
        source.close();
        if (!finished) {
          sendViaPost(message);
        }
      };

      return Promise.resolve();
    }

    function sendMessage(message) {
      clearError();
      addMessage("customer", message);
      setTyping(true);
      sendViaStream(message);
    }

    function openPanel() {
      state.open = true;
      root.setAttribute("data-state", "open");
      if (panel) panel.hidden = false;
      if (launcher) launcher.setAttribute("aria-expanded", "true");
      state.unreadCount = 0;
      updateUnreadBadge();
      if (inputEl) inputEl.focus();
      scrollToBottom();
    }

    function closePanel() {
      state.open = false;
      root.setAttribute("data-state", "closed");
      if (panel) panel.hidden = true;
      if (launcher) launcher.setAttribute("aria-expanded", "false");
    }

    if (launcher) {
      launcher.addEventListener("click", function () {
        if (state.open) {
          closePanel();
        } else {
          openPanel();
        }
      });
    }

    if (minimizeBtn) {
      minimizeBtn.addEventListener("click", closePanel);
    }

    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var message = (inputEl && inputEl.value || "").trim();
        if (!message) return;
        inputEl.value = "";
        sendMessage(message);
      });
    }

    if (inputEl) {
      inputEl.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          if (form) form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
        }
      });
    }

    if (auditPanel) {
      auditPanel.addEventListener("toggle", function () {
        if (!auditPanel.open || state.auditLoaded) return;
        loadAuditTrail();
      });
    }

    function loadAuditTrail() {
      if (!state.conversationId || !auditSteps) {
        if (auditSteps) {
          auditSteps.innerHTML =
            '<li class="nh-widget__audit-empty">Henüz görüntülenecek bir adım yok.</li>';
        }
        return;
      }
      fetch("/api/conversations/" + encodeURIComponent(state.conversationId) + "/audit", {
        credentials: "same-origin"
      })
        .then(function (response) {
          if (!response.ok) throw new Error("audit_failed");
          return response.json();
        })
        .then(function (data) {
          state.auditLoaded = true;
          renderAuditSteps(Array.isArray(data) ? data : data.steps || data.entries || []);
        })
        .catch(function () {
          auditSteps.innerHTML =
            '<li class="nh-widget__audit-empty">Kayıt şu anda getirilemedi.</li>';
        });
    }

    function renderAuditSteps(steps) {
      if (!auditSteps) return;
      auditSteps.innerHTML = "";
      if (!steps.length) {
        auditSteps.innerHTML =
          '<li class="nh-widget__audit-empty">Henüz görüntülenecek bir adım yok.</li>';
        return;
      }
      for (var i = 0; i < steps.length; i++) {
        var step = steps[i];
        var li = document.createElement("li");
        li.className = "nh-widget__audit-step";
        var title = document.createElement("div");
        title.className = "nh-widget__audit-step-title";
        title.textContent = step.summary || step.step_type || "Adım";
        li.appendChild(title);
        if (step.reason) {
          var reason = document.createElement("div");
          reason.className = "nh-widget__audit-step-reason";
          reason.textContent = step.reason;
          li.appendChild(reason);
        }
        auditSteps.appendChild(li);
      }
    }

    // Lightweight connectivity probe — relative URL, uses the documented /health endpoint.
    fetch("/health", { credentials: "same-origin" })
      .then(function (response) {
        connectionStatus.textContent = response.ok ? "Çevrimiçi" : "Bağlantı sorunu";
      })
      .catch(function () {
        if (connectionStatus) connectionStatus.textContent = "Bağlantı yok";
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initLoginPage();
    initChatWidget();
  });
})();
