document.addEventListener("DOMContentLoaded", () => {
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const header = document.getElementById("site-header");
  const navLinks = [...document.querySelectorAll(".nav-link")];
  const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";

  if (window.AOS) {
    AOS.init({ duration: 700, once: true, offset: 80 });
  }

  if (window.gsap && window.ScrollTrigger) {
    gsap.registerPlugin(ScrollTrigger);
    if (!prefersReducedMotion) {
      gsap.from(".hero-maverik-copy .hero-brand-lockup", { y: 30, opacity: 0, duration: 0.8, ease: "power3.out" });
      gsap.from(".hero-feature-orbs .hero-orb", { y: 20, opacity: 0, stagger: 0.1, delay: 0.15, duration: 0.6, ease: "power2.out" });
      gsap.from(".hero-maverik-copy .hero-kicker", { y: 24, opacity: 0, delay: 0.25, duration: 0.7 });
      gsap.from(".hero-maverik-copy .hero-title-caps", { y: 50, opacity: 0, delay: 0.35, duration: 1, ease: "power3.out" });
      gsap.from(".hero-maverik-copy .typed-line", { y: 24, opacity: 0, delay: 0.5, duration: 0.7 });
      gsap.from(".hero-maverik-copy .hero-lead", { y: 24, opacity: 0, delay: 0.58, duration: 0.7 });
      gsap.from(".hero-maverik-copy .hero-ctas", { y: 20, opacity: 0, delay: 0.68, duration: 0.7 });
    }

    ScrollTrigger.create({
      start: "top -80",
      end: 99999,
      onUpdate: (self) => {
        header?.classList.toggle("scrolled", self.scroll() > 80);
      }
    });

    if (!prefersReducedMotion && document.querySelector(".module-tree")) {
      gsap.from(".module-tree .tree-line", {
        scaleY: 0,
        transformOrigin: "top center",
        duration: 0.8,
        stagger: 0.12,
        ease: "power2.out",
        scrollTrigger: {
          trigger: ".module-tree",
          start: "top 78%"
        }
      });
    }
  }

  const typedTarget = document.getElementById("typed-text");
  if (window.Typed && !prefersReducedMotion && typedTarget) {
    new Typed("#typed-text", {
      strings: [
        "Manage your cases with precision.",
        "Never miss a court deadline again.",
        "Your documents — always findable.",
        "Billing that practically runs itself."
      ],
      typeSpeed: 45,
      backSpeed: 25,
      loop: true,
      backDelay: 2000
    });
  } else if (typedTarget) {
    typedTarget.textContent = "Manage your cases with precision.";
  }

  const statsSection = document.getElementById("social-proof");
  let hasCounted = false;
  if (statsSection && "IntersectionObserver" in window) {
    const countObserver = new IntersectionObserver((entries, observer) => {
      if (entries.some((entry) => entry.isIntersecting) && !hasCounted && window.CountUp) {
        hasCounted = true;
        new countUp.CountUp("stat-advocates", 500, { suffix: "+" }).start();
        new countUp.CountUp("stat-uptime", 98, { suffix: "%" }).start();
        new countUp.CountUp("stat-time", 40, { suffix: "%" }).start();
        observer.disconnect();
      }
    }, { threshold: 0.4 });
    countObserver.observe(statsSection);
  }

  if (window.Swiper && document.querySelector(".testimonials-swiper")) {
    new Swiper(".testimonials-swiper", {
      slidesPerView: 1,
      spaceBetween: 24,
      loop: true,
      autoplay: { delay: 4500, disableOnInteraction: false },
      pagination: { el: ".swiper-pagination", clickable: true },
      navigation: { nextEl: ".swiper-button-next", prevEl: ".swiper-button-prev" },
      breakpoints: { 768: { slidesPerView: 2 }, 1024: { slidesPerView: 3 } }
    });
  }

  const particleConfig = {
    particles: {
      number: { value: 60 },
      color: { value: "#A29BFE" },
      shape: { type: "circle" },
      opacity: { value: 0.25 },
      size: { value: 2 },
      line_linked: { enable: true, color: "#6C5CE7", opacity: 0.15 },
      move: { enable: true, speed: 1.2 }
    },
    interactivity: { events: { onhover: { enable: true, mode: "repulse" } } }
  };

  if (window.particlesJS && !prefersReducedMotion) {
    if (document.getElementById("particles-js")) {
      particlesJS("particles-js", particleConfig);
    }
    if (document.getElementById("particles-cta")) {
      particlesJS("particles-cta", { ...particleConfig, particles: { ...particleConfig.particles, number: { value: 44 } } });
    }
  }

  const anchors = [...document.querySelectorAll('a[href^="#"]')];
  anchors.forEach((anchor) => {
    anchor.addEventListener("click", (event) => {
      const targetId = anchor.getAttribute("href");
      if (!targetId || targetId === "#") return;
      const target = document.querySelector(targetId);
      if (!target) return;
      event.preventDefault();
      const top = target.getBoundingClientRect().top + window.scrollY - 80;
      window.scrollTo({ top, behavior: prefersReducedMotion ? "auto" : "smooth" });
    });
  });

  const sectionIds = ["features", "modules", "how-it-works", "pricing", "faq", "contact"];
  const sections = sectionIds.map((id) => document.getElementById(id)).filter(Boolean);
  if ("IntersectionObserver" in window && sections.length) {
    const sectionObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const id = entry.target.id;
        navLinks.forEach((link) => {
          const active = link.getAttribute("href") === `#${id}`;
          link.classList.toggle("active", active);
        });
      });
    }, { threshold: 0.35, rootMargin: "-40% 0px -45% 0px" });
    sections.forEach((section) => sectionObserver.observe(section));
  }

  navLinks.forEach((link) => {
    const href = (link.getAttribute("href") || "").replace(/\/+$/, "") || "/";
    if (href === currentPath) {
      link.classList.add("active");
    }
  });

  const priceEls = [...document.querySelectorAll("[data-price]")];
  const pricingRoot = document.getElementById("pricing");
  if (pricingRoot && priceEls.length) {
    const priceObserver = new MutationObserver(() => {
      priceEls.forEach((el) => {
        el.style.opacity = "0.45";
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            el.style.opacity = "1";
          });
        });
      });
    });
    priceObserver.observe(pricingRoot, { subtree: true, childList: true, characterData: true });
  }

  const moduleData = {
    "document-vault": {
      tag: "CORE MODULE",
      title: "Document Vault",
      description: "Centralized legal file system with governance controls for evidence, pleadings, affidavits, and internal drafts.",
      image: "https://images.unsplash.com/photo-1568992687947-868a62a9f521?w=1200&auto=format&q=80",
      points: ["Smart tagging by matter, client, court, and type", "Version history and rollback for every file", "Secure external sharing with controlled visibility"],
      workflows: ["Upload and classify legal files", "Review/approve drafts before sharing", "Retrieve latest approved versions quickly"],
      roles: ["Partners", "Associates", "Clerks", "Document controllers"],
      inputs: ["Pleadings, affidavits, exhibits, contracts", "Metadata tags and matter references"],
      outputs: ["Organized searchable repository", "Audit-ready version history"],
      outcomes: ["Faster file retrieval", "Reduced version confusion", "Cleaner document governance"]
    },
    "matter-management": {
      tag: "OPERATIONS MODULE",
      title: "Matter Management",
      description: "A structured command center for each case/matter from opening through closure with milestones and ownership.",
      image: "https://images.unsplash.com/photo-1450101499163-c8848c66ca85?w=1200&auto=format&q=80",
      points: ["Unified matter timeline and status tracking", "Advocate/assistant assignment by workflow stage", "Linked tasks, court events, and documentation"],
      workflows: ["Open new matter with structured intake", "Assign ownership and deadlines", "Track status from active to closed"],
      roles: ["Partners", "Matter leads", "Assistants", "Operations"],
      inputs: ["Client instructions", "Matter type and court references"],
      outputs: ["Structured matter records", "Live progress timeline"],
      outcomes: ["Better case visibility", "Clear ownership accountability", "Reduced handoff delays"]
    },
    "court-calendar": {
      tag: "SCHEDULING MODULE",
      title: "Court Calendar",
      description: "Calendar intelligence for hearings, filings, mentions, and internal prep deadlines with reminders and escalation.",
      image: "https://images.unsplash.com/photo-1521791055366-0d553872952f?w=1200&auto=format&q=80",
      points: ["Deadline alerts with escalation rules", "Task dependencies tied to calendar dates", "Hearing readiness checklist visibility"],
      workflows: ["Create hearing/filling events", "Trigger reminders and escalations", "Close pre-hearing checklist items"],
      roles: ["Litigation teams", "Clerks", "Practice managers"],
      inputs: ["Court dates", "Task schedules", "Deadline dependencies"],
      outputs: ["Daily action calendar", "Reminder and escalation logs"],
      outcomes: ["Fewer missed deadlines", "Higher hearing readiness", "Better schedule control"]
    },
    "billing-engine": {
      tag: "FINANCE MODULE",
      title: "Billing Engine",
      description: "Convert legal work into accurate billing outputs while tracking retainers, disbursements, and collections.",
      image: "https://images.unsplash.com/photo-1554224154-22dec7ec8818?w=1200&auto=format&q=80",
      points: ["Invoice generation from billable activity", "Retainer balances and outstanding amount visibility", "Collections follow-up queue and aging analysis"],
      workflows: ["Capture billable work and disbursements", "Generate and issue invoices", "Track payments and follow-ups"],
      roles: ["Partners", "Finance team", "Billing administrators"],
      inputs: ["Time entries", "Retainer balances", "Expense/disbursement records"],
      outputs: ["Invoices", "Aging reports", "Collection actions"],
      outcomes: ["Improved invoice accuracy", "Shorter collection cycles", "Better revenue visibility"]
    },
    "audit-trail": {
      tag: "GOVERNANCE MODULE",
      title: "Audit Trail",
      description: "Action-level traceability across records and workflows for internal review and compliance readiness.",
      image: "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200&auto=format&q=80",
      points: ["Who-changed-what event logs", "Timestamped historical records", "Searchable audit evidence for reviews"],
      workflows: ["Capture action events", "Filter by user/date/module", "Export evidence for review"],
      roles: ["Compliance leads", "Partners", "System administrators"],
      inputs: ["System events across modules"],
      outputs: ["Traceable activity timeline", "Audit extracts"],
      outcomes: ["Stronger governance", "Transparent accountability", "Faster compliance reviews"]
    },
    "access-control": {
      tag: "SECURITY MODULE",
      title: "Access Control",
      description: "Role-based permissions that align data visibility and action rights with legal team responsibilities.",
      image: "https://images.unsplash.com/photo-1563013544-824ae1b704d3?w=1200&auto=format&q=80",
      points: ["Permission matrices by role and module", "Sensitive data visibility controls", "Approval-only operations for restricted actions"],
      workflows: ["Define role policies", "Assign users to permission sets", "Review access anomalies"],
      roles: ["Admins", "Partners", "IT support"],
      inputs: ["Role definitions", "User assignments"],
      outputs: ["Enforced module-level access", "Permission audit reports"],
      outcomes: ["Lower data exposure risk", "Cleaner separation of duties", "Safer operations"]
    },
    "client-portal": {
      tag: "CLIENT EXPERIENCE MODULE",
      title: "Client Portal",
      description: "A secure client-facing surface for approved updates, shared documents, and communication.",
      image: "https://images.unsplash.com/photo-1551836022-d5d88e9218df?w=1200&auto=format&q=80",
      points: ["Case milestone transparency for clients", "Controlled document access permissions", "Secure communication channels"],
      workflows: ["Publish approved updates", "Share selected documents", "Receive client messages"],
      roles: ["Advocates", "Client service teams", "Clients"],
      inputs: ["Approved milestones and files"],
      outputs: ["Client-visible status updates", "Secure communications log"],
      outcomes: ["Higher client trust", "Fewer status follow-up calls", "Better communication clarity"]
    },
    "performance-analytics": {
      tag: "INSIGHTS MODULE",
      title: "Performance Analytics",
      description: "Operational and financial dashboards to monitor throughput, utilization, collection health, and bottlenecks.",
      image: "https://images.unsplash.com/photo-1551281044-8b3c1f8f4f9d?w=1200&auto=format&q=80",
      points: ["Matter cycle-time and workload metrics", "Team productivity and utilization indicators", "Revenue, aging, and forecasting visuals"],
      workflows: ["Aggregate cross-module data", "Render KPI dashboards", "Highlight bottlenecks and risks"],
      roles: ["Partners", "Practice managers", "Finance leads"],
      inputs: ["Matter, task, billing, and client activity data"],
      outputs: ["Operational dashboards", "Trend and risk insights"],
      outcomes: ["Data-driven decisions", "Earlier bottleneck detection", "Better strategic planning"]
    }
  };

  const overlay = document.getElementById("module-modal-overlay");
  const closeBtn = document.getElementById("module-modal-close");
  const tagEl = document.getElementById("module-modal-tag");
  const titleEl = document.getElementById("module-modal-title");
  const descEl = document.getElementById("module-modal-description");
  const imageEl = document.getElementById("module-modal-image");
  const pointsEl = document.getElementById("module-modal-points");
  const workflowsEl = document.getElementById("module-modal-workflows");
  const rolesEl = document.getElementById("module-modal-roles");
  const inputsEl = document.getElementById("module-modal-inputs");
  const outputsEl = document.getElementById("module-modal-outputs");
  const outcomesEl = document.getElementById("module-modal-outcomes");
  const moduleButtons = [...document.querySelectorAll(".module-open-btn[data-module]")];

  const treeNodes = [...document.querySelectorAll("[data-tree-node]")];
  const treePanels = [...document.querySelectorAll("[data-tree-panel]")];
  if (treeNodes.length && treePanels.length) {
    const setActiveTree = (name) => {
      treeNodes.forEach((node) => node.classList.toggle("active", node.dataset.treeNode === name));
      treePanels.forEach((panel) => {
        panel.hidden = panel.dataset.treePanel !== name;
      });
    };
    treeNodes.forEach((node) => {
      node.addEventListener("click", () => {
        const name = node.dataset.treeNode;
        if (name) setActiveTree(name);
      });
    });
    const initial = treeNodes.find((n) => n.classList.contains("active"))?.dataset.treeNode || "intake";
    setActiveTree(initial);
  }

  const closeModal = () => {
    if (!overlay) return;
    overlay.hidden = true;
    document.body.style.overflow = "";
  };

  if (overlay && moduleButtons.length) {
    moduleButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.module;
        const data = moduleData[key] || {};
        const card = btn.closest(".feature-card");
        const fallbackTitle = card?.querySelector("h3")?.textContent?.trim() || "Module details";
        const fallbackDescription = card?.querySelector("p")?.textContent?.trim() || "Detailed explanation for this module.";
        const fallbackTag = card?.querySelector(".feature-badge")?.textContent?.trim()?.toUpperCase() || "MODULE";
        const fallbackPoints = [...(card?.querySelectorAll(".feature-points li") || [])].map((li) => li.textContent.trim());
        const fallbackWorkflows = ["Understand module workflow stages", "Map responsibilities by role", "Apply module in daily operations"];
        const fallbackRoles = ["Partners", "Associates", "Support teams"];
        const fallbackInputs = ["Matter data", "Document updates", "Operational actions"];
        const fallbackOutputs = ["Structured records", "Status updates", "Operational reports"];
        const fallbackOutcomes = ["Greater process clarity", "Improved delivery quality", "Higher operational consistency"];

        tagEl.textContent = data.tag || fallbackTag;
        titleEl.textContent = data.title || fallbackTitle;
        descEl.textContent = data.description || fallbackDescription;
        imageEl.src = data.image || "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?w=1200&auto=format&q=80";
        imageEl.alt = `${data.title || fallbackTitle} visual`;
        const points = Array.isArray(data.points) && data.points.length ? data.points : fallbackPoints;
        pointsEl.innerHTML = points.map((point) => `<li>${point}</li>`).join("");
        const workflows = Array.isArray(data.workflows) && data.workflows.length ? data.workflows : fallbackWorkflows;
        const roles = Array.isArray(data.roles) && data.roles.length ? data.roles : fallbackRoles;
        const inputs = Array.isArray(data.inputs) && data.inputs.length ? data.inputs : fallbackInputs;
        const outputs = Array.isArray(data.outputs) && data.outputs.length ? data.outputs : fallbackOutputs;
        const outcomes = Array.isArray(data.outcomes) && data.outcomes.length ? data.outcomes : fallbackOutcomes;
        if (workflowsEl) workflowsEl.innerHTML = workflows.map((item) => `<li>${item}</li>`).join("");
        if (rolesEl) rolesEl.innerHTML = roles.map((item) => `<li>${item}</li>`).join("");
        if (inputsEl) inputsEl.innerHTML = inputs.map((item) => `<li>${item}</li>`).join("");
        if (outputsEl) outputsEl.innerHTML = outputs.map((item) => `<li>${item}</li>`).join("");
        if (outcomesEl) outcomesEl.innerHTML = outcomes.map((item) => `<li>${item}</li>`).join("");
        overlay.hidden = false;
        document.body.style.overflow = "hidden";
      });
    });

    closeBtn?.addEventListener("click", closeModal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeModal();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeModal();
    });
  }
});
