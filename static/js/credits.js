(() => {
  const root = document.getElementById("creditsRoot");
  const dataEl = document.getElementById("creditsData");
  const wordsEl = document.getElementById("creditsWords");
  const listEl = document.getElementById("projectList");
  const activeLabelEl = document.getElementById("activeProjectLabel");

  if (!root || !dataEl || !wordsEl || !listEl || !activeLabelEl) return;

  const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let payload;
  try {
    payload = JSON.parse(dataEl.textContent.trim());
  } catch (e) {
    console.error("creditsData JSON invalid", e);
    return;
  }

  const projects = Array.isArray(payload.projects) ? payload.projects : [];
  const timing = payload.timing || {};
  const projectHoldMs = Number(timing.projectHoldMs ?? 2200);
  const wordStaggerMs = Number(timing.wordStaggerMs ?? 90);
  const wordFadeMs = Number(timing.wordFadeMs ?? 260);
  const betweenProjectsMs = Number(timing.betweenProjectsMs ?? 450);

  if (!projects.length) return;

  const itemEls = [];
  projects.forEach((p, idx) => {
    const li = document.createElement("li");
    li.className = "project-item";
    if (idx === projects.length - 1) li.classList.add("is-last");

    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = p.name;

    btn.addEventListener("click", () => {
      stopLoop();
      showProject(idx, { fromClick: true });
    });

    li.appendChild(btn);
    listEl.appendChild(li);
    itemEls.push(li);
  });

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function setActiveIndex(i) {
    itemEls.forEach((el, idx) => {
      el.classList.toggle("is-active", idx === i);
      el.classList.toggle("is-complete", idx < i);
    });
  }

  function clearWords() {
    wordsEl.innerHTML = "";
  }

  function normalizeCredits(arr) {
    const words = Array.isArray(arr) ? arr.slice() : [];
    words.sort((a, b) => String(b).localeCompare(String(a)));
    return words;
  }

  async function animateWords(words) {
    clearWords();

    if (prefersReduced) {
      const frag = document.createDocumentFragment();
      words.forEach((w) => {
        const d = document.createElement("div");
        d.className = "word show";
        d.textContent = w;
        frag.appendChild(d);
      });
      wordsEl.appendChild(frag);
      return;
    }

    const frag = document.createDocumentFragment();
    const nodes = words.map((w) => {
      const d = document.createElement("div");
      d.className = "word";
      d.textContent = w;
      frag.appendChild(d);
      return d;
    });

    wordsEl.appendChild(frag);

    for (let i = 0; i < nodes.length; i++) {
      nodes[i].classList.add("show");
      await sleep(wordStaggerMs);
    }
  }

  async function fadeOutWords() {
    if (prefersReduced) {
      clearWords();
      return;
    }

    const nodes = Array.from(wordsEl.querySelectorAll(".word"));
    nodes.forEach((n) => n.classList.remove("show"));
    await sleep(wordFadeMs + 30);
    clearWords();
  }

  let loopToken = 0;
  let looping = true;

  async function showProject(i, opts = {}) {
    setActiveIndex(i);

    const project = projects[i];
    activeLabelEl.textContent = project.name;

    await fadeOutWords();
    await animateWords(normalizeCredits(project.credits));

    if (opts.fromClick) {
      await sleep(projectHoldMs);
      startLoopFrom(i + 1);
    }
  }

  function stopLoop() {
    looping = false;
    loopToken++;
  }

  async function startLoopFrom(startIdx = 0) {
    looping = true;
    const myToken = ++loopToken;

    let i = startIdx % projects.length;

    await showProject(i);
    i = (i + 1) % projects.length;

    while (looping && myToken === loopToken) {
      await sleep(projectHoldMs);
      if (!looping || myToken !== loopToken) break;

      await fadeOutWords();
      await sleep(betweenProjectsMs);

      await showProject(i);
      i = (i + 1) % projects.length;
    }
  }

  startLoopFrom(0);
})();
