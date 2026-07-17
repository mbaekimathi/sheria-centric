(function () {
  'use strict';

  var CONSENT_KEY = 'firm_cookie_consent';
  var LEGACY_KEY = 'firm_cookie_ok';

  function readConsent() {
    try {
      var choice = localStorage.getItem(CONSENT_KEY);
      if (!choice && localStorage.getItem(LEGACY_KEY) === '1') {
        choice = 'accepted';
        localStorage.setItem(CONSENT_KEY, choice);
      }
      return choice;
    } catch (e) {
      return null;
    }
  }

  function writeConsent(choice) {
    try {
      localStorage.setItem(CONSENT_KEY, choice);
      if (choice === 'accepted') localStorage.setItem(LEGACY_KEY, '1');
      else localStorage.removeItem(LEGACY_KEY);
    } catch (e) {}
  }

  function getAnalyticsConfig() {
    var state = window.__firmAnalytics;
    if (state && state.cfg) return state.cfg;
    var el = document.getElementById('firm-analytics-config');
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || '{}');
    } catch (e) {
      return null;
    }
  }

  function injectScript(src, attrs) {
    if (document.querySelector('script[src="' + src + '"]')) return;
    var s = document.createElement('script');
    s.src = src;
    s.async = true;
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        s.setAttribute(k, attrs[k]);
      });
    }
    document.head.appendChild(s);
  }

  function loadFirmAnalytics(cfg) {
    if (!cfg || window.__firmAnalyticsLoaded) return;
    window.__firmAnalyticsLoaded = true;
    var ga4 = (cfg.ga4 || '').trim();
    var gtm = (cfg.gtm || '').trim();
    var meta = (cfg.meta || '').trim();

    if (gtm) {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
      injectScript('https://www.googletagmanager.com/gtm.js?id=' + encodeURIComponent(gtm));
    }

    if (ga4) {
      window.dataLayer = window.dataLayer || [];
      if (typeof window.gtag !== 'function') {
        window.gtag = function () {
          window.dataLayer.push(arguments);
        };
      }
      injectScript('https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(ga4));
      window.gtag('js', new Date());
      window.gtag('config', ga4);
    }

    if (meta) {
      if (!window.fbq) {
        !(function (f, b, e, v, n, t, s) {
          if (f.fbq) return;
          n = f.fbq = function () {
            n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
          };
          if (!f._fbq) f._fbq = n;
          n.push = n;
          n.loaded = true;
          n.version = '2.0';
          n.queue = [];
          t = b.createElement(e);
          t.async = true;
          t.src = v;
          s = b.getElementsByTagName(e)[0];
          s.parentNode.insertBefore(t, s);
        })(window, document, 'script', 'https://connect.facebook.net/en_US/fbevents.js');
      }
      window.fbq('init', meta);
      window.fbq('track', 'PageView');
    }
  }

  function hideCookieBanner(banner) {
    if (!banner) return;
    banner.classList.remove('is-visible');
    window.setTimeout(function () {
      banner.hidden = true;
      banner.setAttribute('aria-hidden', 'true');
    }, 280);
  }

  function showCookieBanner(banner) {
    if (!banner) return;
    banner.hidden = false;
    banner.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(function () {
      banner.classList.add('is-visible');
    });
  }

  function initCookieConsent() {
    var cfg = getAnalyticsConfig();
    var choice = readConsent();
    var banner = document.querySelector('[data-firm-cookie-banner]');

    // Consent required: load analytics only after Accept (or prior Accept).
    if (cfg && cfg.consentRequired && (choice === 'accepted' || window.__firmLoadAnalytics)) {
      loadFirmAnalytics(cfg);
    }

    if (!banner) return;

    if (choice === 'accepted' || choice === 'declined') {
      banner.hidden = true;
      return;
    }

    showCookieBanner(banner);

    var acceptBtn = banner.querySelector('[data-firm-cookie-accept]');
    var declineBtn = banner.querySelector('[data-firm-cookie-decline]');

    if (acceptBtn) {
      acceptBtn.addEventListener('click', function () {
        writeConsent('accepted');
        loadFirmAnalytics(cfg || getAnalyticsConfig());
        hideCookieBanner(banner);
      });
    }
    if (declineBtn) {
      declineBtn.addEventListener('click', function () {
        writeConsent('declined');
        hideCookieBanner(banner);
      });
    }
  }

  initCookieConsent();

  /* Homepage hero logo / main-image cinematic slideshow */
  document.querySelectorAll('[data-firm-hero-slideshow]').forEach(function (root) {
    var slides = Array.prototype.slice.call(root.querySelectorAll('.firm-hero-slide'));
    var dots = Array.prototype.slice.call(root.querySelectorAll('[data-slide-to]'));
    var progress = root.querySelector('.firm-hero-slide-progress span');
    if (slides.length < 2) return;

    var index = 0;
    var timer = null;
    var effectIndex = 0;
    var duration = 5200;
    var effects = ['fade', 'rise', 'swing', 'zoom'];
    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    root.style.setProperty('--slide-duration', duration + 'ms');

    function clearFx(el) {
      if (!el) return;
      el.className = el.className
        .split(/\s+/)
        .filter(function (c) {
          return c && c.indexOf('is-enter-') !== 0 && c.indexOf('is-exit-') !== 0;
        })
        .join(' ');
    }

    function restartProgress() {
      if (!progress) return;
      root.classList.remove('is-playing');
      void progress.offsetWidth;
      if (!reduceMotion) root.classList.add('is-playing');
    }

    function show(nextIndex, options) {
      options = options || {};
      var manual = !!options.manual;
      var next = (nextIndex + slides.length) % slides.length;
      if (next === index && !options.force) return;

      var outgoing = slides[index];
      var incoming = slides[next];
      var effect = effects[effectIndex % effects.length];
      effectIndex += 1;

      slides.forEach(function (el) {
        clearFx(el);
        el.classList.remove('is-active');
      });

      if (!reduceMotion && outgoing && outgoing !== incoming) {
        outgoing.classList.add('is-exit-' + effect);
        outgoing.style.visibility = 'visible';
        outgoing.style.opacity = '1';
        window.setTimeout(function () {
          outgoing.classList.remove('is-exit-' + effect);
          outgoing.style.visibility = '';
          outgoing.style.opacity = '';
        }, 780);
      }

      incoming.classList.add('is-active');
      if (!reduceMotion && outgoing !== incoming) {
        incoming.classList.add('is-enter-' + effect);
      } else if (!reduceMotion && options.force) {
        incoming.classList.add('is-enter-fade');
      }

      dots.forEach(function (el, n) {
        var on = n === next;
        el.classList.toggle('is-active', on);
        el.setAttribute('aria-selected', on ? 'true' : 'false');
      });

      index = next;
      if (!manual) start();
      else restartProgress();
    }

    function start() {
      stop();
      if (reduceMotion) return;
      restartProgress();
      timer = window.setTimeout(function () {
        show(index + 1);
      }, duration);
    }

    function stop() {
      if (timer) window.clearTimeout(timer);
      timer = null;
      root.classList.remove('is-playing');
    }

    dots.forEach(function (dot) {
      dot.addEventListener('click', function () {
        show(parseInt(dot.getAttribute('data-slide-to') || '0', 10), {
          manual: true,
          force: true
        });
        start();
      });
    });

    root.addEventListener('mouseenter', stop);
    root.addEventListener('mouseleave', start);
    root.addEventListener('focusin', stop);
    root.addEventListener('focusout', start);

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stop();
      else start();
    });

    show(0, { force: true, manual: true });
    start();
  });

  var header = document.querySelector('[data-firm-header]');
  var site = document.querySelector('.firm-site');
  if (header) {
    var onScroll = function () {
      header.classList.toggle('is-scrolled', window.scrollY > 16);
      window.dispatchEvent(new Event('firm-header-fit'));
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  var toggle = document.querySelector('[data-firm-menu-toggle]');
  var mobileNav = document.querySelector('[data-firm-mobile-nav]');
  var mobileShell = document.querySelector('[data-firm-mobile-shell]');
  if (mobileShell && mobileShell.parentElement !== document.body) {
    document.body.appendChild(mobileShell);
  }
  var setMenuOpen = function (open) {
    open = !!open;
    if (header) header.classList.toggle('is-open', open);
    if (site) site.classList.toggle('nav-open', open);
    if (mobileShell) {
      if (open) mobileShell.removeAttribute('hidden');
      else mobileShell.setAttribute('hidden', '');
    }
    if (toggle) {
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    }
  };
  if (toggle && mobileNav && mobileShell) {
    toggle.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      setMenuOpen(!site || !site.classList.contains('nav-open'));
    });
    mobileShell.addEventListener('click', function (event) {
      if (event.target.closest('[data-firm-menu-close]')) {
        event.preventDefault();
        setMenuOpen(false);
      }
    });
    mobileNav.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        setMenuOpen(false);
      });
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') setMenuOpen(false);
    });
  }

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if ('IntersectionObserver' in window && !reduceMotion) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -48px 0px' }
    );
    document.querySelectorAll('.firm-reveal').forEach(function (el) {
      observer.observe(el);
    });
  } else {
    document.querySelectorAll('.firm-reveal').forEach(function (el) {
      el.classList.add('is-visible');
    });
  }

  /* Desktop "More" nav + responsive overflow */
  document.querySelectorAll('[data-firm-nav-desktop]').forEach(function (nav) {
    var track = nav.querySelector('[data-firm-nav-track]');
    var more = nav.querySelector('[data-firm-nav-more]');
    var moreBtn = nav.querySelector('[data-firm-nav-more-toggle]');
    var moreMenu = nav.querySelector('[data-firm-nav-more-menu]');
    if (!track || !more || !moreBtn || !moreMenu) return;

    var items = Array.prototype.slice.call(track.querySelectorAll('[data-firm-nav-item]'));
    var fitting = false;

    var closeMore = function () {
      more.classList.remove('is-open');
      moreBtn.setAttribute('aria-expanded', 'false');
    };

    var fitNav = function () {
      if (fitting) return;
      fitting = true;
      closeMore();

      /* Restore all links into the track in original order */
      items.forEach(function (item) {
        track.appendChild(item);
        item.removeAttribute('role');
      });
      moreMenu.innerHTML = '';
      more.hidden = true;
      more.style.display = '';

      /* Measure with More hidden; collapse from the end until track fits */
      var available = nav.clientWidth;
      var used = 0;
      var gap = parseFloat(window.getComputedStyle(track).columnGap || track.style.gap) || 4;
      var widths = items.map(function (item) {
        return item.getBoundingClientRect().width;
      });
      var keep = items.length;

      for (var i = 0; i < items.length; i++) {
        used += widths[i] + (i > 0 ? gap : 0);
      }

      if (used > available && items.length > 2) {
        more.hidden = false;
        /* Force layout so More button width is known */
        var moreWidth = more.getBoundingClientRect().width || 72;
        used = 0;
        keep = 0;
        for (var j = 0; j < items.length; j++) {
          var next = used + widths[j] + (j > 0 ? gap : 0);
          var needMore = j < items.length - 1;
          var limit = available - (needMore ? moreWidth + gap : 0);
          if (next <= limit || keep < 2) {
            used = next;
            keep += 1;
          } else {
            break;
          }
        }
        /* Always leave room for More when not everything fits */
        if (keep >= items.length) keep = items.length - 1;
        if (keep < 2) keep = Math.min(2, items.length - 1);

        for (var k = keep; k < items.length; k++) {
          var moved = items[k];
          moved.setAttribute('role', 'menuitem');
          moreMenu.appendChild(moved);
        }
      }

      more.hidden = moreMenu.childNodes.length === 0;
      fitting = false;
    };

    moreBtn.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      var open = !more.classList.contains('is-open');
      document.querySelectorAll('[data-firm-nav-more].is-open').forEach(function (el) {
        el.classList.remove('is-open');
        var t = el.querySelector('[data-firm-nav-more-toggle]');
        if (t) t.setAttribute('aria-expanded', 'false');
      });
      more.classList.toggle('is-open', open);
      moreBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    moreMenu.addEventListener('click', function (event) {
      if (event.target.closest('a')) closeMore();
    });

    var scheduled = null;
    var scheduleFit = function () {
      if (scheduled) cancelAnimationFrame(scheduled);
      scheduled = requestAnimationFrame(function () {
        scheduled = null;
        fitNav();
      });
    };

    fitNav();
    window.addEventListener('resize', scheduleFit);
    window.addEventListener('firm-header-fit', scheduleFit);
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(scheduleFit);
      ro.observe(nav);
      if (header) ro.observe(header);
    }
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(scheduleFit).catch(function () {});
    }
  });

  document.addEventListener('click', function () {
    document.querySelectorAll('[data-firm-nav-more].is-open').forEach(function (el) {
      el.classList.remove('is-open');
      var t = el.querySelector('[data-firm-nav-more-toggle]');
      if (t) t.setAttribute('aria-expanded', 'false');
    });
  });

  var heroReady = document.querySelector('[data-firm-hero]');
  if (heroReady) heroReady.classList.add('is-ready');

  /* Soft magnetic buttons */
  if (!reduceMotion && window.matchMedia('(pointer: fine)').matches) {
    document.querySelectorAll('[data-firm-magnetic]').forEach(function (btn) {
      btn.addEventListener('pointermove', function (event) {
        var rect = btn.getBoundingClientRect();
        var x = event.clientX - rect.left - rect.width / 2;
        var y = event.clientY - rect.top - rect.height / 2;
        btn.style.transform =
          'translate(' + (x * 0.18).toFixed(1) + 'px,' + (y * 0.22).toFixed(1) + 'px)';
      });
      btn.addEventListener('pointerleave', function () {
        btn.style.transform = '';
      });
    });
  }

  document.querySelectorAll('[data-firm-faq]').forEach(function (list) {
    list.querySelectorAll('.firm-faq-question').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var item = btn.closest('.firm-faq-item');
        if (!item) return;
        var isOpen = item.classList.contains('is-open');
        list.querySelectorAll('.firm-faq-item.is-open').forEach(function (el) {
          el.classList.remove('is-open');
          var q = el.querySelector('.firm-faq-question');
          if (q) q.setAttribute('aria-expanded', 'false');
        });
        if (!isOpen) {
          item.classList.add('is-open');
          btn.setAttribute('aria-expanded', 'true');
        }
      });
    });
  });

  var faqPage = document.querySelector('[data-firm-faq-page]');
  if (faqPage) {
    var faqSearch = faqPage.querySelector('[data-firm-faq-search]');
    var faqCount = faqPage.querySelector('[data-firm-faq-count]');
    var faqEmpty = faqPage.querySelector('[data-firm-faq-empty]');
    var faqCards = Array.prototype.slice.call(
      faqPage.querySelectorAll('[data-firm-faq-card]')
    );
    var topicBtns = Array.prototype.slice.call(
      faqPage.querySelectorAll('[data-firm-faq-topics] [data-faq-topic]')
    );
    var activeTopic = 'all';

    function scrollFaqTarget(el) {
      if (!el) return;
      var header = document.querySelector('.firm-header');
      var offset = (header ? header.getBoundingClientRect().height : 0) + 16;
      var top = el.getBoundingClientRect().top + window.pageYOffset - offset;
      window.scrollTo({
        top: Math.max(0, top),
        behavior: reduceMotion ? 'auto' : 'smooth'
      });
    }

    function applyFaqFilters() {
      var q = faqSearch ? (faqSearch.value || '').trim().toLowerCase() : '';
      var visible = 0;
      faqCards.forEach(function (card) {
        var hay = card.getAttribute('data-faq-text') || '';
        var topics = (card.getAttribute('data-faq-topic') || '').split(/\s+/);
        var topicOk = activeTopic === 'all' || topics.indexOf(activeTopic) !== -1;
        var textOk = !q || hay.indexOf(q) !== -1;
        var show = topicOk && textOk;
        card.hidden = !show;
        if (show) visible += 1;
        if (!show && card.classList.contains('is-open')) {
          card.classList.remove('is-open');
          var btn = card.querySelector('.firm-faq-question');
          if (btn) btn.setAttribute('aria-expanded', 'false');
        }
      });
      if (faqCount) {
        faqCount.textContent =
          visible + ' question' + (visible === 1 ? '' : 's') + (q || activeTopic !== 'all' ? ' shown' : '');
      }
      if (faqEmpty) faqEmpty.hidden = visible !== 0;
    }

    if (faqSearch) {
      faqSearch.addEventListener('input', applyFaqFilters);
      faqSearch.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          faqSearch.blur();
        }
      });
    }

    topicBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        activeTopic = btn.getAttribute('data-faq-topic') || 'all';
        topicBtns.forEach(function (el) {
          var on = el === btn;
          el.classList.toggle('is-active', on);
          el.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        applyFaqFilters();
        if (typeof btn.scrollIntoView === 'function') {
          btn.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', inline: 'center', block: 'nearest' });
        }
        var main = faqPage.querySelector('.firm-faq-main');
        if (main && window.matchMedia('(max-width: 979px)').matches) {
          window.setTimeout(function () {
            scrollFaqTarget(main);
          }, 60);
        }
      });
    });

    faqPage.querySelectorAll('[data-firm-faq-jump]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var id = chip.getAttribute('data-firm-faq-jump');
        var target = id ? document.getElementById(id) : null;
        if (!target) return;
        activeTopic = 'all';
        if (faqSearch) faqSearch.value = '';
        topicBtns.forEach(function (el) {
          var on = el.getAttribute('data-faq-topic') === 'all';
          el.classList.toggle('is-active', on);
          el.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        applyFaqFilters();
        target.hidden = false;
        if (target.classList.contains('firm-faq-item')) {
          var list = target.closest('[data-firm-faq]');
          if (list) {
            list.querySelectorAll('.firm-faq-item.is-open').forEach(function (el) {
              el.classList.remove('is-open');
              var q = el.querySelector('.firm-faq-question');
              if (q) q.setAttribute('aria-expanded', 'false');
            });
          }
          target.classList.add('is-open');
          var openBtn = target.querySelector('.firm-faq-question');
          if (openBtn) openBtn.setAttribute('aria-expanded', 'true');
        }
        window.setTimeout(function () {
          scrollFaqTarget(target);
        }, 40);
      });
    });
  } else {
    var faqSearchLegacy = document.querySelector('[data-firm-faq-search]');
    if (faqSearchLegacy) {
      var faqListLegacy = document.querySelector('[data-firm-faq]');
      var faqCountLegacy = document.querySelector('[data-firm-faq-count]');
      var faqEmptyLegacy = document.querySelector('[data-firm-faq-empty]');
      var faqItemsLegacy = faqListLegacy
        ? Array.prototype.slice.call(faqListLegacy.querySelectorAll('.firm-faq-item'))
        : [];
      faqSearchLegacy.addEventListener('input', function () {
        var q = (faqSearchLegacy.value || '').trim().toLowerCase();
        var visible = 0;
        faqItemsLegacy.forEach(function (item) {
          var hay = item.getAttribute('data-faq-text') || '';
          var show = !q || hay.indexOf(q) !== -1;
          item.hidden = !show;
          if (show) visible += 1;
        });
        if (faqCountLegacy) {
          faqCountLegacy.textContent =
            visible + ' question' + (visible === 1 ? '' : 's') + (q ? ' found' : '');
        }
        if (faqEmptyLegacy) faqEmptyLegacy.hidden = visible !== 0;
      });
    }
  }

  var resultsTabs = document.querySelector('[data-firm-results-tabs]');
  var resultsGrid = document.querySelector('[data-firm-results-grid]');
  if (resultsTabs && resultsGrid) {
    resultsTabs.querySelectorAll('button[data-filter]').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var filter = tab.getAttribute('data-filter');
        resultsTabs.querySelectorAll('button').forEach(function (btn) {
          btn.classList.toggle('is-active', btn === tab);
          btn.setAttribute('aria-selected', btn === tab ? 'true' : 'false');
        });
        resultsGrid.querySelectorAll('[data-result-type]').forEach(function (card) {
          var show = filter === 'all' || card.getAttribute('data-result-type') === filter;
          card.hidden = !show;
        });
      });
    });
  }

  var consultForm = document.querySelector('[data-firm-consultation-form]');
  if (consultForm) {
    consultForm.addEventListener('submit', function (event) {
      var name = consultForm.querySelector('[name="full_name"]');
      var phone = consultForm.querySelector('[name="phone"]');
      var email = consultForm.querySelector('[name="email"]');
      var message = '';
      if (!name || !name.value.trim()) {
        message = 'Please enter your name.';
      } else if (
        (!phone || !phone.value.trim()) &&
        (!email || !email.value.trim())
      ) {
        message = 'Please provide a phone number or email address.';
      }
      if (message) {
        event.preventDefault();
        window.alert(message);
      }
    });
  }

  if (!reduceMotion && 'IntersectionObserver' in window) {
    var statsObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var el = entry.target.querySelector('strong');
          if (!el) return;
          var raw = (el.textContent || '').trim();
          var match = raw.match(/^(\d+)(.*)$/);
          if (!match) {
            statsObserver.unobserve(entry.target);
            return;
          }
          var target = parseInt(match[1], 10);
          var suffix = match[2] || '';
          if (!target || target > 5000) {
            statsObserver.unobserve(entry.target);
            return;
          }
          var start = performance.now();
          var duration = 1000;
          var tick = function (now) {
            var t = Math.min(1, (now - start) / duration);
            var eased = 1 - Math.pow(1 - t, 3);
            el.textContent = Math.round(target * eased) + suffix;
            if (t < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
          statsObserver.unobserve(entry.target);
        });
      },
      { threshold: 0.4 }
    );
    document.querySelectorAll('.firm-stat, .firm-metric').forEach(function (stat) {
      statsObserver.observe(stat);
    });
  }

  var heroEl = document.querySelector('.firm-hero');
  var beam = document.querySelector('.firm-hero-beam');
  var floatMark = document.querySelector('[data-firm-float="mark"]');
  var floatCopy = document.querySelector('[data-firm-float="copy"]');
  if (heroEl && !reduceMotion && window.matchMedia('(pointer: fine)').matches) {
    heroEl.addEventListener(
      'pointermove',
      function (event) {
        var rect = heroEl.getBoundingClientRect();
        var nx = (event.clientX - rect.left) / rect.width - 0.5;
        var ny = (event.clientY - rect.top) / rect.height - 0.5;
        if (beam) {
          beam.style.transform =
            'translate(' + (nx * 10).toFixed(2) + '%,' + (ny * 8).toFixed(2) + '%)';
        }
        if (floatMark) {
          floatMark.style.setProperty('--float-x', (nx * 12).toFixed(1) + 'px');
          floatMark.style.setProperty('--float-y', (ny * 10).toFixed(1) + 'px');
          floatMark.style.setProperty('--float-r', (nx * 2).toFixed(2) + 'deg');
        }
        if (floatCopy) {
          floatCopy.style.transform =
            'translate3d(' + (nx * -5).toFixed(1) + 'px,' + (ny * -4).toFixed(1) + 'px,0)';
        }
      },
      { passive: true }
    );
    heroEl.addEventListener('pointerleave', function () {
      if (beam) beam.style.transform = '';
      if (floatMark) {
        floatMark.style.setProperty('--float-x', '0px');
        floatMark.style.setProperty('--float-y', '0px');
        floatMark.style.setProperty('--float-r', '0deg');
      }
      if (floatCopy) floatCopy.style.transform = '';
    });
  }

  if (!reduceMotion) {
    var pageHero = document.querySelector('.firm-page-hero-rich');
    if (pageHero) {
      window.addEventListener(
        'scroll',
        function () {
          var y = Math.min(48, window.scrollY * 0.08);
          pageHero.style.setProperty('--hero-shift', y.toFixed(1) + 'px');
        },
        { passive: true }
      );
    }
  }

  var blogPage = document.querySelector('[data-firm-blog-page]');
  if (blogPage) {
    var blogSearch = blogPage.querySelector('[data-firm-blog-search]');
    var blogCount = blogPage.querySelector('[data-firm-blog-count]');
    var blogEmpty = blogPage.querySelector('[data-firm-blog-empty]');
    var blogCards = Array.prototype.slice.call(
      blogPage.querySelectorAll('[data-firm-blog-card]')
    );
    var authorBtns = Array.prototype.slice.call(
      blogPage.querySelectorAll('[data-firm-blog-authors] [data-blog-author]')
    );
    var activeAuthor = 'all';

    function applyBlogFilters() {
      var q = blogSearch ? (blogSearch.value || '').trim().toLowerCase() : '';
      var visible = 0;
      blogCards.forEach(function (card) {
        var hay = card.getAttribute('data-blog-text') || '';
        var author = card.getAttribute('data-blog-author') || '';
        var authorOk = activeAuthor === 'all' || author === activeAuthor;
        var textOk = !q || hay.indexOf(q) !== -1;
        var show = authorOk && textOk;
        card.hidden = !show;
        if (show) visible += 1;
      });
      if (blogCount) {
        blogCount.textContent =
          visible + ' article' + (visible === 1 ? '' : 's') + (q || activeAuthor !== 'all' ? ' shown' : '');
      }
      if (blogEmpty) blogEmpty.hidden = visible !== 0;
    }

    if (blogSearch) {
      blogSearch.addEventListener('input', applyBlogFilters);
      blogSearch.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          blogSearch.blur();
        }
      });
    }

    authorBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        activeAuthor = btn.getAttribute('data-blog-author') || 'all';
        authorBtns.forEach(function (el) {
          var on = el === btn;
          el.classList.toggle('is-active', on);
          el.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        applyBlogFilters();
        if (typeof btn.scrollIntoView === 'function') {
          btn.scrollIntoView({
            behavior: reduceMotion ? 'auto' : 'smooth',
            inline: 'center',
            block: 'nearest'
          });
        }
      });
    });
  }

  /* Homepage scroll progress, cue, light parallax */
  var homeProgress = document.querySelector('[data-firm-home-progress] span');
  var scrollCue = document.querySelector('[data-firm-scroll-cue]');
  var parallaxEls = Array.prototype.slice.call(document.querySelectorAll('[data-firm-parallax]'));
  if (homeProgress || scrollCue || parallaxEls.length) {
    var onHomeScroll = function () {
      var doc = document.documentElement;
      var max = Math.max(1, doc.scrollHeight - window.innerHeight);
      var ratio = Math.min(1, Math.max(0, window.scrollY / max));
      if (homeProgress) homeProgress.style.width = (ratio * 100).toFixed(2) + '%';
      if (scrollCue) scrollCue.classList.toggle('is-hidden', window.scrollY > 48);
      if (!reduceMotion) {
        parallaxEls.forEach(function (el) {
          var rect = el.getBoundingClientRect();
          var mid = rect.top + rect.height / 2 - window.innerHeight / 2;
          var shift = Math.max(-18, Math.min(18, mid * -0.04));
          el.style.setProperty('--parallax-y', shift.toFixed(1) + 'px');
        });
      }
    };
    window.addEventListener('scroll', onHomeScroll, { passive: true });
    onHomeScroll();
  }
})();
