"use strict";
/* Calage de plans — page locale.
   Tout le calcul est fait par le serveur Python local ; cette page affiche, fait cliquer et
   envoie les choix. Coordonnées : le serveur envoie la géométrie RELATIVE à une origine, la page
   travaille en coordonnées absolues pour tout ce qui est échangé (points, zone). */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

// =========================================================================== utilitaires
async function api(url, corps) {
  const opts = corps === undefined ? {} : {
    method: "POST",
    headers: corps instanceof Blob ? {} : { "Content-Type": "application/json" },
    body: corps instanceof Blob ? corps : JSON.stringify(corps),
  };
  const r = await fetch(url, opts);
  let data;
  try { data = await r.json(); } catch { throw new Error("Réponse illisible du serveur."); }
  if (!r.ok || data.erreur) throw new Error(data.erreur || `Erreur ${r.status}`);
  return data;
}

let minuteurToast = null;
function toast(msg, info = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = info ? "info" : "";
  t.hidden = false;
  clearTimeout(minuteurToast);
  minuteurToast = setTimeout(() => (t.hidden = true), info ? 3500 : 7000);
}

async function avecAttente(texte, fn) {
  $("#attente-texte").textContent = texte;
  $("#attente").hidden = false;
  try { return await fn(); }
  catch (e) { toast(e.message); throw e; }
  finally { $("#attente").hidden = true; }
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (x, n = 3) => Number(x).toLocaleString("fr-CH", { minimumFractionDigits: n, maximumFractionDigits: n });

// Similitude en nombres complexes : w = a·z + b
const SIM_ID = { ar: 1, ai: 0, br: 0, bi: 0 };
function simDepuisCalage(c) {
  const t = (c.rotation_deg * Math.PI) / 180;
  return { ar: c.echelle * Math.cos(t), ai: c.echelle * Math.sin(t), br: c.translation[0], bi: c.translation[1] };
}
function appliquerSim(s, x, y) { return [s.ar * x - s.ai * y + s.br, s.ai * x + s.ar * y + s.bi]; }
function inverserSim(s, x, y) {
  const dx = x - s.br, dy = y - s.bi, m = s.ar * s.ar + s.ai * s.ai;
  return [(s.ar * dx + s.ai * dy) / m, (-s.ai * dx + s.ar * dy) / m];
}

// =========================================================================== visualiseur
class Vue {
  constructor(canvas) {
    this.c = canvas;
    this.ctx = canvas.getContext("2d");
    this.couches = [];
    this.origine = [0, 0];          // origine absolue du cadre de la vue
    this.k = 1;                      // pixels par unité
    this.cx = 0; this.cy = 0;        // centre de la vue (relatif à l'origine)
    this.marques = [];               // {x, y (absolu), texte, couleur}
    this.zone = null;                // {pts: [[x,y]...] absolus, fermee}
    this.surClic = null; this.surDoubleClic = null;
    this._installer();
    new ResizeObserver(() => this.redim()).observe(canvas);
  }
  get dpr() { return window.devicePixelRatio || 1; }
  redim() {
    const r = this.c.getBoundingClientRect();
    if (!r.width) return;
    this.c.width = Math.round(r.width * this.dpr);
    this.c.height = Math.round(r.height * this.dpr);
    this.dessiner();
  }
  vider() { this.couches = []; this.marques = []; this.zone = null; }
  ajouterCouche(c) { this.couches.push(Object.assign({ sim: SIM_ID, epaisseur: 1, textes: true }, c)); }

  // coordonnées ------------------------------------------------------------------------
  ecranVersAbs(px, py) {
    const r = this.c.getBoundingClientRect();
    return [this.cx + (px - r.width / 2) / this.k + this.origine[0],
            this.cy - (py - r.height / 2) / this.k + this.origine[1]];
  }
  absVersEcran(x, y) {
    const r = this.c.getBoundingClientRect();
    return [r.width / 2 + this.k * (x - this.origine[0] - this.cx),
            r.height / 2 - this.k * (y - this.origine[1] - this.cy)];
  }
  ajuster(emp) {
    if (!emp) return;
    const r = this.c.getBoundingClientRect();
    const w = Math.max(emp[2] - emp[0], 1e-6), h = Math.max(emp[3] - emp[1], 1e-6);
    this.k = 0.9 * Math.min(r.width / w, r.height / h) || 1;
    this.cx = (emp[0] + emp[2]) / 2 - this.origine[0];
    this.cy = (emp[1] + emp[3]) / 2 - this.origine[1];
    this.dessiner();
  }
  // emprise d'une couche dans le cadre (absolu)
  empriseCouche(c) {
    const e = c.geo.emprise, s = c.sim;
    const pts = [[e[0], e[1]], [e[2], e[1]], [e[2], e[3]], [e[0], e[3]]].map(([x, y]) => appliquerSim(s, x, y));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  // dessin -----------------------------------------------------------------------------
  _chemins(geo) {
    if (!geo._chemins) {
      geo._chemins = geo.groupes.map((g) => {
        const p = new Path2D();
        for (const l of g.lignes) {
          p.moveTo(l[0], l[1]);
          for (let i = 2; i < l.length; i += 2) p.lineTo(l[i], l[i + 1]);
        }
        return p;
      });
    }
    return geo._chemins;
  }
  dessiner() {
    const ctx = this.ctx, d = this.dpr, W = this.c.width / d, H = this.c.height / d;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, this.c.width, this.c.height);
    for (const c of this.couches) {
      if (c.visible === false) continue;
      const s = c.sim, g = c.geo, k = this.k;
      // w = a·p + b'  avec  b' = a·Og + b − Of   (p relatif à l'origine de la géométrie)
      const og = g.origine;
      const bpr = s.ar * og[0] - s.ai * og[1] + s.br - this.origine[0];
      const bpi = s.ai * og[0] + s.ar * og[1] + s.bi - this.origine[1];
      const m11 = k * s.ar, m12 = -k * s.ai, m21 = -k * s.ai, m22 = -k * s.ar;
      const dx = W / 2 + k * (bpr - this.cx), dy = H / 2 - k * (bpi - this.cy);
      ctx.setTransform(d * m11, d * m12, d * m21, d * m22, d * dx, d * dy);
      const echelle = k * Math.hypot(s.ar, s.ai);
      const chemins = this._chemins(g);
      g.groupes.forEach((gr, i) => {
        const coul = c.couleur(gr);
        if (!coul) return;
        ctx.strokeStyle = coul;
        ctx.lineWidth = (c.epaisseurDe ? c.epaisseurDe(gr) : c.epaisseur) / echelle;
        ctx.stroke(chemins[i]);
      });
      if (c.textes && g.textes.length) {
        ctx.setTransform(d, 0, 0, d, 0, 0);
        const rotA = Math.atan2(s.ai, s.ar);
        for (const t of g.textes) {
          const coul = c.couleur({ l: t[0], t: t[1] });
          if (!coul) continue;
          const taille = t[4] * echelle;
          if (taille < 4 || taille > 400) continue;
          const [ax, ay] = appliquerSim(s, t[2] + og[0], t[3] + og[1]);
          const [px, py] = this.absVersEcran(ax, ay);
          if (px < -200 || py < -50 || px > W + 200 || py > H + 50) continue;
          ctx.save();
          ctx.translate(px, py);
          ctx.rotate(-((t[5] * Math.PI) / 180 + rotA));
          ctx.fillStyle = coul;
          ctx.font = `${taille.toFixed(1)}px system-ui, sans-serif`;
          ctx.fillText(t[6], 0, 0);
          ctx.restore();
        }
      }
    }
    // zone
    ctx.setTransform(d, 0, 0, d, 0, 0);
    if (this.zone && this.zone.pts.length) {
      ctx.beginPath();
      this.zone.pts.forEach(([x, y], i) => {
        const [px, py] = this.absVersEcran(x, y);
        i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      });
      if (this.zone.fermee) ctx.closePath();
      ctx.setLineDash([8, 5]);
      ctx.strokeStyle = "#1f5fbf"; ctx.lineWidth = 2; ctx.stroke();
      ctx.setLineDash([]);
      if (this.zone.fermee) { ctx.fillStyle = "rgba(31,95,191,.06)"; ctx.fill(); }
      for (const [x, y] of this.zone.pts) {
        const [px, py] = this.absVersEcran(x, y);
        ctx.fillStyle = "#1f5fbf"; ctx.fillRect(px - 3, py - 3, 6, 6);
      }
    }
    // marques (points de calage)
    for (const m of this.marques) {
      const [px, py] = this.absVersEcran(m.x, m.y);
      ctx.strokeStyle = m.couleur; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, 7, 0, 2 * Math.PI); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(px - 11, py); ctx.lineTo(px + 11, py); ctx.moveTo(px, py - 11); ctx.lineTo(px, py + 11); ctx.stroke();
      ctx.font = "bold 13px system-ui, sans-serif";
      ctx.lineWidth = 3; ctx.strokeStyle = "#fff"; ctx.strokeText(m.texte, px + 10, py - 10);
      ctx.fillStyle = m.couleur; ctx.fillText(m.texte, px + 10, py - 10);
    }
  }

  // interactions -----------------------------------------------------------------------
  _installer() {
    let depart = null, glisse = false;
    this.c.addEventListener("pointerdown", (e) => {
      depart = { x: e.offsetX, y: e.offsetY, cx: this.cx, cy: this.cy, bouton: e.button };
      glisse = false;
      this.c.setPointerCapture(e.pointerId);
    });
    this.c.addEventListener("pointermove", (e) => {
      if (!depart) return;
      const dx = e.offsetX - depart.x, dy = e.offsetY - depart.y;
      if (!glisse && Math.hypot(dx, dy) > 4) glisse = true;
      if (glisse) {
        this.cx = depart.cx - dx / this.k;
        this.cy = depart.cy + dy / this.k;
        this.c.style.cursor = "grabbing";
        this.dessiner();
      }
    });
    this.c.addEventListener("pointerup", (e) => {
      this.c.style.cursor = "";
      if (depart && !glisse && depart.bouton === 0 && this.surClic) {
        const [x, y] = this.ecranVersAbs(e.offsetX, e.offsetY);
        this.surClic(x, y, e);
      }
      depart = null;
    });
    this.c.addEventListener("dblclick", (e) => {
      if (this.surDoubleClic) {
        const [x, y] = this.ecranVersAbs(e.offsetX, e.offsetY);
        this.surDoubleClic(x, y, e);
      }
    });
    this.c.addEventListener("contextmenu", (e) => e.preventDefault());
    this.c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const f = Math.exp(-e.deltaY * 0.0015);
      const [ax, ay] = this.ecranVersAbs(e.offsetX, e.offsetY);
      this.k *= f;
      const [bx, by] = this.ecranVersAbs(e.offsetX, e.offsetY);
      this.cx += ax - bx; this.cy += ay - by;
      this.dessiner();
    }, { passive: false });
  }
}

// =========================================================================== état
const etat = {
  geo: { ref: null, plan: null },
  infos: {},
  points: [{ plan: null, ref: null }, { plan: null, ref: null }, { plan: null, ref: null }],
  calage: null,
  sim: null,
  gardes: new Set(),
  familles: [],
  zone: null,
  dessinZone: false,
  surligne: null,
};
const vPlan = new Vue($("#c-plan"));
const vRef = new Vue($("#c-ref"));
const vUnique = new Vue($("#c-unique"));
const COUL_REF = "#a3a9b3";
const COUL_ROUGE = "#d62020";
const NOMS_POINTS = ["1", "2", "3 (contrôle)"];
const COUL_POINTS = ["#1f5fbf", "#1f5fbf", "#e07000"];

function afficherVues(mode) {
  $("#accueil").hidden = mode !== "accueil";
  $("#vue-double").hidden = mode !== "double";
  $("#vue-unique").hidden = mode !== "unique";
  $("#voir-superposition").hidden = mode === "unique" && !!etat.sim;
  $("#voir-cote-a-cote").hidden = mode !== "unique";
  requestAnimationFrame(() => { vPlan.redim(); vRef.redim(); vUnique.redim(); });
}

// =========================================================================== onglets / étapes
$$(".onglet").forEach((b) => b.addEventListener("click", () => {
  $$(".onglet").forEach((x) => x.classList.toggle("actif", x === b));
  const o = b.dataset.onglet;
  $("#onglet-projet").hidden = o !== "projet";
  $("#onglet-indice").hidden = o !== "indice";
  if (o === "indice") { chargerProjets(); afficherVues(etat.vueIndice ? "unique" : "accueil"); if (etat.vueIndice) montrerResultat(etat.vueIndice, "#titre-unique"); }
  else afficherVues(etat.modeVueProjet || (etat.geo.plan || etat.geo.ref ? "double" : "accueil"));
  if (o === "indice" && !etat.vueIndice) $("#accueil").innerHTML = "<p>Choisissez le projet et déposez le nouvel indice à gauche.</p>";
  if (o === "projet") $("#accueil").innerHTML = "<p>Déposez la <b>référence MN95</b> et le <b>plan à caler</b> à gauche.</p>";
}));

function ouvrirEtape(n) {
  $$("#onglet-projet .etape").forEach((e) => {
    const k = +e.dataset.etape;
    e.classList.toggle("ouverte", k === n);
    e.classList.toggle("faite", k < n);
  });
  etat.etape = n;
}
$$("#onglet-projet .etape h2").forEach((h) => h.addEventListener("click", () => {
  const e = h.parentElement, n = +e.dataset.etape;
  if (e.classList.contains("faite")) {
    ouvrirEtape(n);
    if (n <= 2) montrerCoteACote();
    else if (n === 3) montrerNettoyage();
  }
}));

// =========================================================================== dépôt de fichiers
function installerDepot(label, auDepot) {
  const input = label.querySelector("input");
  input.addEventListener("change", () => { if (input.files.length) auDepot([...input.files]); input.value = ""; });
  label.addEventListener("dragover", (e) => { e.preventDefault(); label.classList.add("survol"); });
  label.addEventListener("dragleave", () => label.classList.remove("survol"));
  label.addEventListener("drop", (e) => {
    e.preventDefault(); label.classList.remove("survol");
    if (e.dataTransfer.files.length) auDepot([...e.dataTransfer.files]);
  });
}
async function televerser(role, fichier) {
  return api(`/api/fichier?role=${role}&nom=${encodeURIComponent(fichier.name)}`, fichier);
}

$$("#onglet-projet .depot").forEach((label) => installerDepot(label, async (fichiers) => {
  const role = label.dataset.role;
  try {
    await avecAttente("Lecture du plan (conversion, liaison des xrefs, explosion des blocs)…", async () => {
      if (role === "xref") {
        for (const f of fichiers) await televerser("xref", f);
        label.querySelector(".etat").textContent = fichiers.map((f) => f.name).join(", ");
        label.classList.add("charge");
        if (etat.geo.plan) await ouvrir("plan", $('.depot[data-role="plan"]'));
      } else {
        await televerser(role, fichiers[0]);
        await ouvrir(role, label);
      }
    });
  } catch { /* message déjà affiché */ }
}));

async function ouvrir(role, label) {
  const r = await api("/api/ouvrir", { role });
  etat.geo[role] = r.geometrie;
  etat.infos[role] = r;
  label.classList.add("charge");
  label.querySelector(".etat").textContent = `${r.nom} — ${r.nb_entites} objets, unité : ${r.unite}`;
  if (role === "plan") {
    etat.points.forEach((p) => (p.plan = null));
    etat.calage = null; etat.sim = null;
    etat.gardes = new Set(r.geometrie.calques.map((c) => c.nom));
    etat.zone = null;
    const base = r.nom.replace(/\.[^.]+$/, "");
    $("#nom-projet").value = base;
    $("#prefixe").value = `DWG_${base} - `;
  }
  // alertes
  const al = [];
  for (const k of ["ref", "plan"]) {
    const inf = etat.infos[k];
    if (!inf) continue;
    for (const a of inf.alertes) al.push(`<div class="alerte">${esc(a)}</div>`);
    for (const x of inf.xrefs) if (!x.statut.startsWith("VIDE"))
      al.push(`<div class="info">Xref « ${esc(x.nom)} » : ${esc(x.statut)} (${x.nb_entites} objets)</div>`);
  }
  $("#alertes-fichiers").innerHTML = al.join("");
  $("#vers-calage").disabled = !(etat.geo.ref && etat.geo.plan);
  preparerVuesDoubles();
  montrerCoteACote();
  if (role === "plan") vPlan.ajuster(etat.geo.plan.emprise); else vRef.ajuster(etat.geo.ref.emprise);
}

$("#vers-calage").addEventListener("click", () => { ouvrirEtape(2); afficherPoints(); montrerCoteACote(); });

// =========================================================================== calage
function couleurCalque(geo) { return (g) => geo.calques[g.l].couleur; }

function preparerVuesDoubles() {
  for (const [v, role] of [[vPlan, "plan"], [vRef, "ref"]]) {
    const geo = etat.geo[role];
    v.vider();
    if (!geo) continue;
    v.origine = geo.origine;
    v.ajouterCouche({ geo, couleur: role === "ref" ? () => "#3a3f47" : couleurCalque(geo) });
  }
  majMarques();
}
function montrerCoteACote() { etat.modeVueProjet = "double"; afficherVues(etat.geo.plan || etat.geo.ref ? "double" : "accueil"); }

function majMarques() {
  vPlan.marques = []; vRef.marques = [];
  etat.points.forEach((p, i) => {
    if (p.plan) vPlan.marques.push({ x: p.plan.x, y: p.plan.y, texte: NOMS_POINTS[i], couleur: COUL_POINTS[i] });
    if (p.ref) vRef.marques.push({ x: p.ref.x, y: p.ref.y, texte: NOMS_POINTS[i], couleur: COUL_POINTS[i] });
  });
  vPlan.dessiner(); vRef.dessiner();
}

function afficherPoints() {
  const ol = $("#liste-points");
  const prochain = etat.points.findIndex((p) => !p.plan || !p.ref);
  ol.innerHTML = etat.points.map((p, i) => {
    const puce = (pt, cote) => pt
      ? `<span class="puce ${pt.genre === "libre" ? "libre" : "ok"}" title="${pt.genre}">${cote} ✓</span>`
      : `<span class="puce">${cote}</span>`;
    return `<li class="${i === prochain ? "courant" : ""}"><span>Point ${NOMS_POINTS[i]}</span>
      ${puce(p.plan, "plan")}${puce(p.ref, "réf.")}<button data-effacer="${i}" title="Effacer ce point">✕</button></li>`;
  }).join("");
  ol.querySelectorAll("[data-effacer]").forEach((b) => b.addEventListener("click", () => {
    const i = +b.dataset.effacer;
    etat.points[i] = { plan: null, ref: null };
    afficherPoints(); majMarques(); calculerCalage();
  }));
}

async function cliquerPoint(cote, vue, x, y) {
  if (etat.etape !== 2) return;
  const i = etat.points.findIndex((p) => !p[cote]);
  if (i < 0) { toast("Les 3 points sont saisis sur ce plan : effacez-en un pour le refaire.", true); return; }
  let pt = { x, y, genre: "libre" };
  try {
    const r = await api("/api/accroche", { role: cote, x, y, rayon: 14 / vue.k });
    if (!r.aucun) pt = r;
    else toast("Aucun sommet ni intersection sous le curseur : point pris tel quel (zoomez pour plus de précision).", true);
  } catch (e) { toast(e.message); return; }
  etat.points[i][cote] = pt;
  afficherPoints(); majMarques();
  calculerCalage();
}
vPlan.surClic = (x, y) => cliquerPoint("plan", vPlan, x, y);
vRef.surClic = (x, y) => cliquerPoint("ref", vRef, x, y);

async function calculerCalage() {
  const complets = etat.points.map((p) => p.plan && p.ref);
  const zone = $("#resultat-calage");
  if (!complets[0] || !complets[1]) {
    etat.calage = null; etat.sim = null;
    zone.innerHTML = "";
    $("#vers-nettoyage").disabled = true; $("#voir-superposition").disabled = true;
    return;
  }
  const paire = (p) => [[p.plan.x, p.plan.y], [p.ref.x, p.ref.y]];
  try {
    const r = await api("/api/calage", {
      calage: [paire(etat.points[0]), paire(etat.points[1])],
      controle: complets[2] ? [paire(etat.points[2])] : [],
    });
    etat.calage = r; etat.sim = simDepuisCalage(r);
    const cls = { "bon": "bon", "à vérifier": "averifier", "mauvais": "mauvais", "sans contrôle": "sanscontrole" }[r.verdict];
    const ech = r.echelle_arrondie ? `${r.echelle} <small>(mesurée ${r.echelle_brute.toPrecision(6)})</small>` : r.echelle_brute.toPrecision(6);
    zone.innerHTML = `<div class="verdict ${cls}"><b>${esc(r.message)}</b>
      <table>
        <tr><td>Échelle</td><td>${ech}</td></tr>
        <tr><td>Rotation</td><td>${fmt(r.rotation_deg, 4)}°</td></tr>
        <tr><td>Translation</td><td>E ${fmt(r.translation[0])} / N ${fmt(r.translation[1])}</td></tr>
        <tr><td>Écart points 1-2</td><td>${r.ecarts_calage_m.map((e) => fmt(e * 1000, 0) + " mm").join(" / ")}</td></tr>
        ${r.ecarts_controle_m.length ? `<tr><td>Écart point 3</td><td><b>${fmt(r.ecarts_controle_m[0] * 1000, 0)} mm</b></td></tr>` : ""}
      </table></div>`;
    $("#vers-nettoyage").disabled = false; $("#voir-superposition").disabled = false;
    if (!$("#vue-unique").hidden) montrerSuperposition();
  } catch (e) { toast(e.message); }
}

function montrerSuperposition() {
  if (!etat.sim) return;
  etat.modeVueProjet = "unique";
  vUnique.vider();
  vUnique.origine = etat.geo.ref.origine;
  vUnique.ajouterCouche({ geo: etat.geo.ref, couleur: () => COUL_REF, textes: false });
  vUnique.ajouterCouche({ geo: etat.geo.plan, sim: etat.sim, couleur: (g) => couleurPlanSuperpose(g), epaisseurDe: (g) => (g.l === etat.surligne ? 3 : 1) });
  if (etat.zone) vUnique.zone = etat.zone;
  $("#case-couleurs").hidden = false;
  $("#titre-unique").textContent = etat.etape >= 3 ? "Nettoyage — plan calé sur la référence (gris)" : "Superposition — plan calé sur la référence (gris)";
  afficherVues("unique");
  requestAnimationFrame(() => vUnique.ajuster(vUnique.empriseCouche(vUnique.couches[1])));
}
$("#voir-superposition").addEventListener("click", montrerSuperposition);
$("#voir-cote-a-cote").addEventListener("click", montrerCoteACote);

$("#vers-nettoyage").addEventListener("click", async () => {
  const r = etat.calage;
  if (r.verdict === "mauvais" && !confirm("Le point de contrôle indique un calage FAUX. Continuer quand même ?")) return;
  if (r.verdict === "sans contrôle" && !confirm("Aucun point de contrôle : le calage n'est pas vérifié. Continuer ?")) return;
  ouvrirEtape(3);
  await preparerNettoyage();
  montrerNettoyage();
});

// =========================================================================== nettoyage
function typesSupprimes() {
  const s = new Set();
  for (const f of etat.familles) if (f.coche) f.types.forEach((t) => s.add(t));
  return s;
}
function couleurPlanSuperpose(g) {
  const geo = etat.geo.plan;
  if (etat.etape >= 3) {
    if (!etat.gardes.has(geo.calques[g.l].nom)) return null;
    if (typesSupprimes().has(geo.types[g.t])) return null;
  }
  if (g.l === etat.surligne) return "#f0a000";
  return $("#couleurs-calques").checked ? geo.calques[g.l].couleur : COUL_ROUGE;
}
$("#couleurs-calques").addEventListener("change", () => vUnique.dessiner());

async function preparerNettoyage() {
  if (!etat.familles.length) {
    const r = await api("/api/familles");
    etat.familles = r.familles;
  }
  $("#familles").innerHTML = etat.familles.map((f, i) =>
    `<label><input type="checkbox" data-famille="${i}" ${f.coche ? "checked" : ""}> ${esc(f.libelle)} <small>(${f.n})</small></label>`).join("");
  $$("#familles input").forEach((c) => c.addEventListener("change", () => {
    etat.familles[+c.dataset.famille].coche = c.checked; vUnique.dessiner();
  }));
  afficherCalques();
}
function montrerNettoyage() { montrerSuperposition(); }

function afficherCalques() {
  const geo = etat.geo.plan, filtre = $("#filtre-calques").value.toLowerCase();
  const ul = $("#liste-calques");
  ul.innerHTML = geo.calques.map((c, i) => {
    if (filtre && !c.nom.toLowerCase().includes(filtre)) return "";
    const g = etat.gardes.has(c.nom);
    const detail = Object.entries(c.types).map(([t, n]) => `${t} ${n}`).join(", ");
    return `<li data-i="${i}" class="${g ? "" : "ecarte"} ${i === etat.surligne ? "surligne" : ""}" title="${esc(detail)}">
      <input type="checkbox" ${g ? "checked" : ""}><span class="pastille" style="background:${c.couleur}"></span>
      <span class="nom">${esc(c.nom)}</span><span class="n">${c.n}</span></li>`;
  }).join("");
  $("#compte-calques").textContent = `(${etat.gardes.size} gardés sur ${geo.calques.length})`;
  ul.querySelectorAll("li").forEach((li) => {
    const i = +li.dataset.i, nom = geo.calques[i].nom;
    li.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? etat.gardes.add(nom) : etat.gardes.delete(nom);
      li.classList.toggle("ecarte", !e.target.checked);
      $("#compte-calques").textContent = `(${etat.gardes.size} gardés sur ${geo.calques.length})`;
      vUnique.dessiner();
    });
    li.addEventListener("mouseenter", () => { etat.surligne = i; vUnique.dessiner(); });
    li.addEventListener("mouseleave", () => { etat.surligne = null; vUnique.dessiner(); });
  });
}
$("#filtre-calques").addEventListener("input", afficherCalques);
$("#tout-cocher").addEventListener("click", () => { etat.geo.plan.calques.forEach((c) => etat.gardes.add(c.nom)); afficherCalques(); vUnique.dessiner(); });
$("#tout-decocher").addEventListener("click", () => { etat.gardes.clear(); afficherCalques(); vUnique.dessiner(); });

// zone
$("#dessiner-zone").addEventListener("click", () => {
  etat.dessinZone = !etat.dessinZone;
  if (etat.dessinZone) { etat.zone = { pts: [], fermee: false }; vUnique.zone = etat.zone; }
  $("#dessiner-zone").classList.toggle("actif", etat.dessinZone);
  $("#dessiner-zone").textContent = etat.dessinZone ? "Dessin en cours… (double-clic pour fermer)" : "Redessiner la zone";
  vUnique.dessiner();
});
$("#effacer-zone").addEventListener("click", () => {
  etat.zone = null; vUnique.zone = null; etat.dessinZone = false;
  $("#dessiner-zone").classList.remove("actif"); $("#dessiner-zone").textContent = "Dessiner la zone";
  $("#effacer-zone").disabled = true; vUnique.dessiner();
});

vUnique.surClic = (x, y) => {
  if (etat.ongletIndice) return;
  if (etat.etape === 3 && etat.dessinZone) {
    etat.zone.pts.push([x, y]); vUnique.dessiner(); return;
  }
  if (etat.etape === 3) identifierObjet(x, y);
};
vUnique.surDoubleClic = () => {
  if (etat.etape !== 3 || !etat.dessinZone) return;
  const z = etat.zone;
  // le double-clic a ajouté deux fois le dernier sommet
  while (z.pts.length > 1) {
    const a = z.pts[z.pts.length - 1], b = z.pts[z.pts.length - 2];
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) * vUnique.k < 6) z.pts.pop(); else break;
  }
  if (z.pts.length < 3) { toast("La zone doit avoir au moins 3 sommets.", true); return; }
  z.fermee = true; etat.dessinZone = false;
  $("#dessiner-zone").classList.remove("actif"); $("#dessiner-zone").textContent = "Redessiner la zone";
  $("#effacer-zone").disabled = false;
  vUnique.dessiner();
};

// Identifier l'objet le plus proche du clic (parmi les objets visibles du plan)
function identifierObjet(x, y) {
  const geo = etat.geo.plan, s = etat.sim;
  const [px, py] = inverserSim(s, x, y);
  const rx = px - geo.origine[0], ry = py - geo.origine[1];
  const tol = 10 / (vUnique.k * Math.hypot(s.ar, s.ai));
  let meilleur = null, dmin = tol;
  geo.groupes.forEach((g) => {
    if (!couleurPlanSuperpose(g)) return;
    for (const l of g.lignes) {
      for (let i = 0; i + 3 < l.length; i += 2) {
        const d = distSeg(rx, ry, l[i], l[i + 1], l[i + 2], l[i + 3]);
        if (d < dmin) { dmin = d; meilleur = g; }
      }
    }
  });
  const boite = $("#info-objet");
  if (!meilleur) { boite.hidden = true; etat.surligne = null; vUnique.dessiner(); return; }
  const c = geo.calques[meilleur.l];
  etat.surligne = meilleur.l;
  boite.innerHTML = `Calque <b>« ${esc(c.nom)} »</b> — ${esc(geo.types[meilleur.t])}
    <button id="ecarter-ce-calque">Écarter ce calque</button>`;
  boite.hidden = false;
  $("#ecarter-ce-calque").addEventListener("click", () => {
    etat.gardes.delete(c.nom); etat.surligne = null; boite.hidden = true;
    afficherCalques(); vUnique.dessiner();
  });
  afficherCalques(); vUnique.dessiner();
  const li = $(`#liste-calques li[data-i="${meilleur.l}"]`);
  if (li) li.scrollIntoView({ block: "nearest" });
}
function distSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1, L = dx * dx + dy * dy;
  let t = L ? ((px - x1) * dx + (py - y1) * dy) / L : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - x1 - t * dx, py - y1 - t * dy);
}

// =========================================================================== export
$("#exporter").addEventListener("click", async () => {
  if (etat.dessinZone) { toast("Terminez d'abord la zone (double-clic) ou effacez-la.", true); return; }
  const corps = {
    projet: $("#nom-projet").value.trim() || "projet",
    prefixe: $("#prefixe").value,
    calques: [...etat.gardes],
    familles: etat.familles.filter((f) => f.coche).map((f) => f.libelle),
    zone: etat.zone && etat.zone.fermee ? etat.zone.pts : null,
  };
  try {
    const r = await avecAttente("Création du DXF calé et de la recette…", () => api("/api/exporter", corps));
    $("#info-objet").hidden = true;
    ouvrirEtape(4);
    afficherRapport($("#resultat-export"), r, true);
    etat.vueProjetResultat = r;
    montrerResultat(r, "#titre-unique");
  } catch { /* déjà signalé */ }
});

const NOMS_TYPES = { LINE: "lignes", LWPOLYLINE: "polylignes", POLYLINE: "polylignes (anciennes)", CIRCLE: "cercles",
  ARC: "arcs", TEXT: "textes", MTEXT: "textes multilignes", HATCH: "hachures", DIMENSION: "cotations",
  MULTILEADER: "lignes de repère", ATTDEF: "attributs", ATTRIB: "attributs", SOLID: "surfaces pleines",
  SPLINE: "courbes", ELLIPSE: "ellipses", MLINE: "multilignes", POINT: "points" };
const nomType = (t) => NOMS_TYPES[t] || t;

function afficherRapport(cible, rep, creation) {
  const r = rep.rapport, mode = r.mode;
  const titres = {
    "identique": creation ? "DXF calé créé, recette enregistrée" : "Calé : même origine que l'indice appris",
    "recalé": "Calé AVEC CORRECTION AUTOMATIQUE : le plan avait bougé",
    "forcé": "Produit SANS CONTRÔLE (forcé) : à vérifier soigneusement",
    "refus": "Refusé : aucun fichier produit",
  };
  const cls = { "identique": "identique", "recalé": "recale", "forcé": "force", "refus": "refus" }[mode];
  let h = `<div class="bandeau ${cls}">${titres[mode] || mode}`;
  if (mode !== "identique" || !creation) h += `<p>${esc(r.controle.message || "")}</p>`;
  h += `</div>`;
  if (rep.telecharger) h += `<a class="telecharger" href="${rep.telecharger}">Télécharger le DXF calé</a>`;
  if (mode === "refus" && !creation) {
    h += `<p class="aide">Que faire : refaire le calage de ce plan dans l'onglet « Nouveau projet ».</p>
      <button id="forcer" class="danger">Produire quand même (déconseillé)</button>`;
  }
  const al = r.alertes.filter((a) => !(r.controle.message && a.includes(r.controle.message)));
  if (al.length) h += `<div class="alertes">${al.map((a) => `<div class="alerte">${esc(a)}</div>`).join("")}</div>`;
  if (r.infos.length) h += `<div class="alertes">${r.infos.map((a) => `<div class="info">${esc(a)}</div>`).join("")}</div>`;
  if (r.fichier_produit) {
    const ecartes = Object.values(r.calques_ecartes).reduce((a, b) => a + b, 0);
    const supp = Object.entries(r.types_supprimes).map(([t, n]) => `${nomType(t)} ${n}`).join(", ");
    const contenu = Object.entries(r.entites_sortie_par_type).sort().map(([t, n]) => `${nomType(t)} ${n}`).join(", ");
    h += `<table class="chiffres">
      <tr><td>Objets</td><td>${r.nb_entites_plan} dans le plan → <b>${r.nb_entites_sortie}</b> dans le DXF</td></tr>
      <tr><td>Calques écartés</td><td>${ecartes} objets</td></tr>
      ${supp ? `<tr><td>Supprimés</td><td>${supp}</td></tr>` : ""}
      ${r.hors_zone ? `<tr><td>Hors zone</td><td>${r.hors_zone}</td></tr>` : ""}
      ${Object.keys(r.non_transformables).length ? `<tr><td><b>Non transformés</b></td><td>${esc(JSON.stringify(r.non_transformables))}</td></tr>` : ""}
      <tr><td>Contenu</td><td>${contenu}</td></tr>
      <tr><td>Concordance</td><td>${Math.round(100 * (r.controle.part_ancres_identiques || 0))} % des repères retrouvés tels quels${
        r.controle.part_ancres_apres_correction != null ? `, ${Math.round(100 * r.controle.part_ancres_apres_correction)} % après correction` : ""}</td></tr>
    </table>`;
    if (rep.chemin_sortie) h += `<p class="aide">Enregistré aussi dans : ${esc(rep.chemin_sortie)}</p>`;
  }
  h += `<details><summary>Rapport complet</summary><pre>${esc(rep.texte)}</pre></details>`;
  if (creation) h += `<p class="aide">Pour les indices suivants : onglet <b>« Nouvel indice »</b>, projet « ${esc(rep.projet)} ».</p>`;
  cible.innerHTML = h;
  const bf = cible.querySelector("#forcer");
  if (bf) bf.addEventListener("click", () => {
    if (confirm("Le contrôle a échoué : le fichier produit risque d'être FAUX. Produire quand même ?")) calerIndice(true);
  });
}

function montrerResultat(rep, titreSel) {
  vUnique.vider();
  const ref = rep.reference || etat.geo.ref, res = rep.resultat;
  if (!ref && !res) { afficherVues("accueil"); return; }
  vUnique.origine = (ref || res).origine;
  if (ref) vUnique.ajouterCouche({ geo: ref, couleur: () => COUL_REF, textes: false });
  if (res) vUnique.ajouterCouche({ geo: res, couleur: () => COUL_ROUGE });
  $(titreSel).textContent = res ? "Résultat : DXF calé (rouge) sur la référence (gris)" : "Référence du projet (aucun fichier produit)";
  $("#case-couleurs").hidden = true;
  afficherVues("unique");
  requestAnimationFrame(() => vUnique.ajuster(res ? res.emprise : ref.emprise));
}

// =========================================================================== nouvel indice
async function chargerProjets() {
  const r = await api("/api/projets");
  const sel = $("#choix-projet"), avant = sel.value;
  sel.innerHTML = r.projets.length
    ? r.projets.map((p) => `<option value="${esc(p.nom)}">${esc(p.nom)}</option>`).join("")
    : `<option value="">(aucun projet : créez-en un dans « Nouveau projet »)</option>`;
  if (avant && r.projets.some((p) => p.nom === avant)) sel.value = avant;
  etat.projets = r.projets;
  majInfoProjet();
}
function majInfoProjet() {
  const p = (etat.projets || []).find((x) => x.nom === $("#choix-projet").value);
  $("#info-projet").textContent = p ? `Appris sur « ${p.plan} » le ${p.cree_le.replace("T", " à ")}` : "";
  $("#caler-indice").disabled = !(p && etat.indiceCharge);
}
$("#choix-projet").addEventListener("change", majInfoProjet);

$$("#onglet-indice .depot").forEach((label) => installerDepot(label, async (fichiers) => {
  const role = label.dataset.role;
  try {
    await avecAttente("Envoi du fichier…", async () => {
      if (role === "indice_xref") for (const f of fichiers) await televerser(role, f);
      else { await televerser(role, fichiers[0]); etat.indiceCharge = true; }
    });
    label.classList.add("charge");
    label.querySelector(".etat").textContent = fichiers.map((f) => f.name).join(", ");
    majInfoProjet();
  } catch { /* déjà signalé */ }
}));

async function calerIndice(forcer = false) {
  try {
    const r = await avecAttente("Calage du nouvel indice (contrôle, nettoyage, transformation)…", () =>
      api("/api/indice", { projet: $("#choix-projet").value, forcer, ecarter_nouveaux: $("#ecarter-nouveaux").checked }));
    afficherRapport($("#resultat-indice"), r, false);
    etat.vueIndice = r;
    montrerResultat(r, "#titre-unique");
  } catch { /* déjà signalé */ }
}
$("#caler-indice").addEventListener("click", () => calerIndice(false));

// boutons « Tout voir »
$$("[data-ajuster]").forEach((b) => b.addEventListener("click", () => {
  const v = { plan: vPlan, ref: vRef, unique: vUnique }[b.dataset.ajuster];
  if (!v.couches.length) return;
  const emps = v.couches.map((c) => v.empriseCouche(c));
  const cible = v === vUnique && v.couches.length > 1 ? emps[emps.length - 1] : emps[0];
  v.ajuster(cible);
}));

Object.defineProperty(etat, "ongletIndice", { get: () => !$("#onglet-indice").hidden });
ouvrirEtape(1);
afficherVues("accueil");
