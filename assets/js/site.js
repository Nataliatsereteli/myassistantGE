/* ============================================================================
   Клиентский JS. Никаких зависимостей и сборки — обычный ES2015+.
   Сайт полностью работоспособен и без него: JS добавляет только удобство.
   ========================================================================== */
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Мобильное меню -------------------------------------------------- */
  var burger = document.querySelector("[data-burger]");
  var mnav = document.querySelector("[data-mobile-nav]");
  if (burger && mnav) {
    burger.addEventListener("click", function () {
      var open = burger.getAttribute("aria-expanded") === "true";
      burger.setAttribute("aria-expanded", String(!open));
      mnav.classList.toggle("is-open", !open);
    });
    mnav.addEventListener("click", function (e) {
      if (e.target.closest("a")) {
        burger.setAttribute("aria-expanded", "false");
        mnav.classList.remove("is-open");
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && mnav.classList.contains("is-open")) {
        burger.setAttribute("aria-expanded", "false");
        mnav.classList.remove("is-open");
        burger.focus();
      }
    });
  }

  /* ---- Тень у прилипшей шапки ------------------------------------------ */
  var header = document.querySelector(".site-header");
  if (header) {
    var onScroll = function () {
      header.classList.toggle("is-stuck", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---- Появление блоков при прокрутке ---------------------------------- */
  var revealables = document.querySelectorAll(".reveal, .rule--draw");
  if (revealables.length) {
    if (reduced || !("IntersectionObserver" in window)) {
      revealables.forEach(function (el) { el.classList.add("is-in"); });
    } else {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            io.unobserve(entry.target);
          }
        });
      }, { rootMargin: "0px 0px -12% 0px", threshold: 0.05 });
      revealables.forEach(function (el) { io.observe(el); });
    }
  }

  /* ---- Плавающая кнопка «написать» (только на мобильных) --------------- */
  var fab = document.querySelector("[data-fab]");
  if (fab) {
    var toggleFab = function () {
      fab.classList.toggle("is-visible", window.scrollY > 560);
    };
    toggleFab();
    window.addEventListener("scroll", toggleFab, { passive: true });
  }

  /* ---- Запоминаем выбранный язык --------------------------------------- */
  var htmlLang = document.documentElement.getAttribute("lang");
  if (htmlLang) {
    try { localStorage.setItem("preferred-lang", htmlLang); } catch (e) { /* приватный режим */ }
  }

  /* ---- Форма заявки ----------------------------------------------------
     Отправка через любой сервис форм для статичных сайтов (Web3Forms / Formspree / …).
     Адрес задаётся в content/site.yml -> forms.endpoint.
     Если endpoint не задан, форма на странице вообще не выводится, а вместо
     неё показываются прямые контакты, поэтому здесь достаточно проверки.     */
  var form = document.querySelector("[data-lead-form]");
  if (form) {
    var status = form.querySelector("[data-form-status]");
    var submit = form.querySelector("[type=submit]");
    var msgs = {
      sending: form.dataset.msgSending || "Отправляем…",
      ok: form.dataset.msgOk || "Спасибо! Я свяжусь с вами в ближайшее время.",
      fail: form.dataset.msgFail || "Не получилось отправить. Напишите, пожалуйста, в WhatsApp или Telegram."
    };

    var show = function (state, text) {
      if (!status) return;
      status.hidden = false;
      status.setAttribute("data-state", state);
      status.textContent = text;
    };

    form.addEventListener("submit", function (e) {
      // Ловушка для ботов: настоящий человек это поле не заполнит.
      var hp = form.querySelector("[name=_honey]");
      if (hp && hp.value) { e.preventDefault(); return; }

      if (!form.action || form.action.indexOf("mailto:") === 0) return; // обычная отправка

      e.preventDefault();
      if (!form.reportValidity()) return;

      submit && (submit.disabled = true);
      show("pending", msgs.sending);

      fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" }
      })
        .then(function (r) {
          if (!r.ok) throw new Error(String(r.status));
          form.reset();
          show("ok", msgs.ok);
          if (form.dataset.thanksUrl) {
            window.setTimeout(function () { window.location.href = form.dataset.thanksUrl; }, 600);
          }
        })
        .catch(function () { show("fail", msgs.fail); })
        .then(function () { submit && (submit.disabled = false); });
    });
  }

  /* ---- Подсветка активного раздела в навигации по якорям --------------- */
  var anchorNav = document.querySelectorAll('.nav a[href*="#"]');
  if (anchorNav.length && "IntersectionObserver" in window) {
    var map = {};
    anchorNav.forEach(function (a) {
      var id = a.getAttribute("href").split("#")[1];
      var target = id && document.getElementById(id);
      if (target) map[id] = a;
    });
    var sectionObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var link = map[entry.target.id];
        if (link && entry.isIntersecting) {
          Object.keys(map).forEach(function (k) { map[k].removeAttribute("aria-current"); });
          link.setAttribute("aria-current", "page");
        }
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(map).forEach(function (id) { sectionObserver.observe(document.getElementById(id)); });
  }
})();
