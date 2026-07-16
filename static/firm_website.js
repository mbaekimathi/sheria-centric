(function () {
  'use strict';

  var header = document.querySelector('[data-firm-header]');
  if (header) {
    var onScroll = function () {
      header.classList.toggle('is-scrolled', window.scrollY > 8);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  var toggle = document.querySelector('[data-firm-menu-toggle]');
  var mobileNav = document.querySelector('[data-firm-mobile-nav]');
  if (toggle && mobileNav) {
    toggle.addEventListener('click', function () {
      var open = mobileNav.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    mobileNav.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        mobileNav.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
    );
    document.querySelectorAll('.firm-reveal').forEach(function (el) {
      observer.observe(el);
    });
  } else {
    document.querySelectorAll('.firm-reveal').forEach(function (el) {
      el.classList.add('is-visible');
    });
  }

  document.querySelectorAll('[data-firm-faq] .firm-faq-question').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var item = btn.closest('.firm-faq-item');
      if (!item) return;
      var isOpen = item.classList.contains('is-open');
      item.parentElement.querySelectorAll('.firm-faq-item.is-open').forEach(function (el) {
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
})();
