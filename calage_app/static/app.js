"use strict";
/* Calage de plans — page locale.
   Tout le calcul est fait par le serveur Python local ; la page affiche, fait cliquer et envoie
   les choix. Le serveur envoie la géométrie RELATIVE à une origine ; tout ce qui est échangé
   (points, axes, zone) est en coordonnées absolues. */

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
  t.textContent = msg; t.className = info ? "info" : ""; t.hidden = false;
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
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (x, n = 3) => Number(x).toLocaleString("fr-CH", { minimumFractionDigits: n, maximumFractionDigits: n });

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
function distSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1, L = dx * dx + dy * dy;
  let t = L ? ((px - x1) * dx + (py - y1) * dy) / L : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - x1 - t * dx, py - y1 - t * dy);
}

// =========================================================================== visualiseur
class Vue {
  constructor(canvas) {
    this.c = canvas;
    this.ctx = canvas.getContext("2d");
    this.couches = [];
    this.origine = [0, 0];
    this.k = 1; this.cx = 0; this.cy = 0;
    this.marques = [];        // points : {x, y, texte, couleur}
    this.traits = [];         // axes : {s: [x1,y1,x2,y2], texte, couleur}
    this.survol = null;       // accroche affichée avant le clic : {x, y, genre} ou {segment}
    this.zone = null;
    this.surClic = null; this.surDoubleClic = null; this.surSurvol = null; this.surSortie = null;
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
  vider() { this.couches = []; this.marques = []; this.traits = []; this.zone = null; this.survol = null; }
  ajouterCouche(c) { this.couches.push(Object.assign({ sim: SIM_ID, epaisseur: 1, textes: true }, c)); }
  ecranVersAbs(px, py) {
    const r = this.c.getBoundingClientRect();
    return [this.cx + (px - r.width / 2) / this.k + this.origine[0], this.cy - (py - r.height / 2) / this.k + this.origine[1]];
  }
  absVersEcran(x, y) {
    const r = this.c.getBoundingClientRect();
    return [r.width / 2 + this.k * (x - this.origine[0] - this.cx), r.height / 2 - this.k * (y - this.origine[1] - this.cy)];
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
  empriseCouche(c) {
    const e = c.geo.emprise, s = c.sim;
    const pts = [[e[0], e[1]], [e[2], e[1]], [e[2], e[3]], [e[0], e[3]]].map(([x, y]) => appliquerSim(s, x, y));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  _chemins(geo) {
    if (!geo._chemins) {
      geo._chemins = geo.groupes.map((g) => {
        const p = new Path2D();
        for (const l of g.lignes) { p.moveTo(l[0], l[1]); for (let i = 2; i < l.length; i += 2) p.lineTo(l[i], l[i + 1]); }
        return p;
      });
    }
    return geo._chemins;
  }
  dessiner() {
    const ctx = this.ctx, d = this.dpr, W = this.c.width / d, H = this.c.height / d;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, this.c.width, this.c.height);
    for (const c of this.couches) {
      if (c.visible === false) continue;
      const s = c.sim, g = c.geo, k = this.k, og = g.origine;
      const bpr = s.ar * og[0] - s.ai * og[1] + s.br - this.origine[0];
      const bpi = s.ai * og[0] + s.ar * og[1] + s.bi - this.origine[1];
      ctx.setTransform(d * k * s.ar, -d * k * s.ai, -d * k * s.ai, -d * k * s.ar,
        d * (W / 2 + k * (bpr - this.cx)), d * (H / 2 - k * (bpi - this.cy)));
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
          ctx.save(); ctx.translate(px, py); ctx.rotate(-((t[5] * Math.PI) / 180 + rotA));
          ctx.fillStyle = coul; ctx.font = `${taille.toFixed(1)}px system-ui, sans-serif`;
          ctx.fillText(t[6], 0, 0); ctx.restore();
        }
      }
    }
    ctx.setTransform(d, 0, 0, d, 0, 0);
    // zone
    if (this.zone && this.zone.pts.length) {
      ctx.beginPath();
      this.zone.pts.forEach(([x, y], i) => { const [px, py] = this.absVersEcran(x, y); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
      if (this.zone.fermee) ctx.closePath();
      ctx.setLineDash([8, 5]); ctx.strokeStyle = "#1f5fbf"; ctx.lineWidth = 2; ctx.stroke(); ctx.setLineDash([]);
      for (const [x, y] of this.zone.pts) { const [px, py] = this.absVersEcran(x, y); ctx.fillStyle = "#1f5fbf"; ctx.fillRect(px - 3, py - 3, 6, 6); }
    }
    // axes saisis
    for (const t of this.traits) {
      const [a, b] = [this.absVersEcran(t.s[0], t.s[1]), this.absVersEcran(t.s[2], t.s[3])];
      ctx.strokeStyle = t.couleur; ctx.lineWidth = 4; ctx.globalAlpha = 0.75;
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.globalAlpha = 1;
      this._etiquette(t.texte, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, t.couleur);
    }
    // points saisis
    for (const m of this.marques) {
      const [px, py] = this.absVersEcran(m.x, m.y);
      ctx.strokeStyle = m.couleur; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, 7, 0, 2 * Math.PI); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(px - 11, py); ctx.lineTo(px + 11, py); ctx.moveTo(px, py - 11); ctx.lineTo(px, py + 11); ctx.stroke();
      this._etiquette(m.texte, px, py, m.couleur);
    }
    // accroche au survol (symboles type DAO)
    const sv = this.survol;
    if (sv) {
      ctx.strokeStyle = "#ff8a00"; ctx.lineWidth = 2;
      if (sv.segment) {
        const [a, b] = [this.absVersEcran(sv.segment[0], sv.segment[1]), this.absVersEcran(sv.segment[2], sv.segment[3])];
        ctx.lineWidth = 5; ctx.globalAlpha = 0.6;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.globalAlpha = 1;
      } else {
        const [px, py] = this.absVersEcran(sv.x, sv.y);
        ctx.beginPath();
        if (sv.genre === "centre") ctx.arc(px, py, 7, 0, 2 * Math.PI);
        else if (sv.genre === "intersection") { ctx.moveTo(px - 7, py - 7); ctx.lineTo(px + 7, py + 7); ctx.moveTo(px + 7, py - 7); ctx.lineTo(px - 7, py + 7); }
        else ctx.rect(px - 6, py - 6, 12, 12);
        ctx.stroke();
      }
    }
  }
  _etiquette(texte, px, py, couleur) {
    const ctx = this.ctx;
    ctx.font = "bold 13px system-ui, sans-serif";
    ctx.lineWidth = 3; ctx.strokeStyle = "#fff"; ctx.strokeText(texte, px + 10, py - 10);
    ctx.fillStyle = couleur; ctx.fillText(texte, px + 10, py - 10);
  }
  _installer() {
    let depart = null, glisse = false;
    this.c.addEventListener("pointerdown", (e) => {
      depart = { x: e.offsetX, y: e.offsetY, cx: this.cx, cy: this.cy, bouton: e.button };
      glisse = false; this.c.setPointerCapture(e.pointerId);
    });
    this.c.addEventListener("pointermove", (e) => {
      if (depart) {
        const dx = e.offsetX - depart.x, dy = e.offsetY - depart.y;
        if (!glisse && Math.hypot(dx, dy) > 4) glisse = true;
        if (glisse) {
          this.cx = depart.cx - dx / this.k; this.cy = depart.cy + dy / this.k;
          this.c.style.cursor = "grabbing"; this.dessiner(); return;
        }
      }
      if (this.surSurvol) { const [x, y] = this.ecranVersAbs(e.offsetX, e.offsetY); this.surSurvol(x, y, e); }
    });
    this.c.addEventListener("pointerup", (e) => {
      this.c.style.cursor = "";
      if (depart && !glisse && depart.bouton === 0 && this.surClic) {
        const [x, y] = this.ecranVersAbs(e.offsetX, e.offsetY); this.surClic(x, y, e);
      }
      depart = null;
    });
    this.c.addEventListener("pointerleave", () => { if (this.surSortie) this.surSortie(); });
    this.c.addEventListener("dblclick", (e) => {
      if (this.surDoubleClic) { const [x, y] = this.ecranVersAbs(e.offsetX, e.offsetY); this.surDoubleClic(x, y, e); }
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
  ecran: "accueil",
  chantiers: [], typesExistants: [], chantier: null,     // chantier ouvert (nom)
  coches: new Set(),                                     // indices affichés sur l'écran chantier
  geo: { ref: null, plan: null }, infos: {},
  elements: [], calage: null, sim: null, auto: false, etape: 1, vueSuperposee: false,
  gardes: new Set(), familles: [], zone: null, dessinZone: false,
  surligne: null, selection: null, modeEcarter: false, historique: [],
  typeIndice: null, indiceCharge: false,
};
const vPlan = new Vue($("#c-plan")), vRef = new Vue($("#c-ref")), vUnique = new Vue($("#c-unique"));
const COUL_REF = "#a3a9b3", COUL_ROUGE = "#d62020", COUL_CAL = "#1f5fbf", COUL_VERIF = "#e07000";
const PALETTE = ["#d62020", "#1f5fbf", "#1b9e3b", "#9b30c9", "#e07000", "#0f9fb0", "#b5338a", "#5c6f00"];
const dateCourte = (d) => (d || "").replace("T", " ").slice(0, 16);

function afficherVues(mode, texteAccueil) {
  $("#accueil").hidden = mode !== "accueil";
  $("#vue-double").hidden = mode !== "double";
  $("#vue-unique").hidden = mode !== "unique";
  if (texteAccueil) $("#accueil-texte").innerHTML = texteAccueil;
  $("#info-objet").hidden = true; $("#bulle").hidden = true;
  requestAnimationFrame(() => { vPlan.redim(); vRef.redim(); vUnique.redim(); });
}

// =========================================================================== navigation
function aller(ecran) {
  etat.ecran = ecran;
  for (const e of ["accueil", "chantier", "ajout", "indice"]) $(`#e-${e}`).hidden = e !== ecran;
  const ar = [`<a href="#" data-nav="accueil">Mes chantiers</a>`];
  if (ecran !== "accueil" && etat.chantier) {
    ar.push(`<span class="sep">›</span>`);
    ar.push(ecran === "chantier" ? `<span class="courant">${esc(etat.chantier)}</span>` : `<a href="#" data-nav="chantier">${esc(etat.chantier)}</a>`);
  }
  if (ecran === "ajout") ar.push(`<span class="sep">›</span><span class="courant">Ajouter un plan</span>`);
  if (ecran === "indice") ar.push(`<span class="sep">›</span><span class="courant">Nouvel indice — ${esc(etat.typeIndice)}</span>`);
  $("#ariane").innerHTML = ar.join("");
  $$("#ariane a").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    if (etat.ecran === "ajout" && etat.etape > 1 && etat.etape < 4 && !confirm("Quitter sans enregistrer ce plan ?")) return;
    if (a.dataset.nav === "accueil") ouvrirAccueil(); else ouvrirChantier(etat.chantier);
  }));
}

async function chargerChantiers() {
  const r = await api("/api/chantiers");
  etat.chantiers = r.chantiers;
  etat.typesExistants = r.types_existants || [];
  return r;
}
const chantierCourant = () => etat.chantiers.find((c) => c.nom === etat.chantier);

// =========================================================================== MES CHANTIERS
async function ouvrirAccueil() {
  etat.chantier = null;
  aller("accueil");
  try { await chargerChantiers(); } catch (e) { toast(e.message); }
  const liste = [...etat.chantiers].sort((a, b) => (b.derniere_activite || "").localeCompare(a.derniere_activite || "") || a.nom.localeCompare(b.nom));
  $("#cartes-chantiers").innerHTML = liste.length ? liste.map((c) => {
    const nbIndices = c.types.reduce((n, t) => n + t.indices.length, 0);
    return `<div class="carte carte-chantier" data-chantier="${esc(c.nom)}"><b>${esc(c.nom)}</b>
      <small>${c.types.length} plan(s) · ${nbIndices} indice(s)${c.derniere_activite ? " · dernier le " + esc(dateCourte(c.derniere_activite)) : ""}</small></div>`;
  }).join("") : `<p class="vide">Aucun chantier pour l'instant. Commencez par en créer un.</p>`;
  $$(".carte-chantier").forEach((c) => c.addEventListener("click", () => ouvrirChantier(c.dataset.chantier)));
  $("#form-chantier").hidden = true;
  $("#btn-nouveau-chantier").hidden = false;
  afficherVues("accueil", liste.length ? "Choisissez un chantier à gauche, ou créez-en un nouveau." : "Créez votre premier chantier : un nom et la référence MN95 du géomètre.");
}

$("#btn-nouveau-chantier").addEventListener("click", () => {
  $("#form-chantier").hidden = false; $("#btn-nouveau-chantier").hidden = true;
  $("#nc-nom").value = ""; const d = $("#nc-depot"); d.classList.remove("charge"); d.querySelector(".etat").textContent = "";
  etat.refNouveauChantier = false; majBoutonCreer();
  $("#nc-nom").focus();
});
$("#nc-annuler").addEventListener("click", () => { $("#form-chantier").hidden = true; $("#btn-nouveau-chantier").hidden = false; afficherVues("accueil"); });
$("#nc-nom").addEventListener("input", majBoutonCreer);
function majBoutonCreer() { $("#nc-creer").disabled = !($("#nc-nom").value.trim() && etat.refNouveauChantier); }
installerDepot($("#nc-depot"), async (fichiers) => {
  try {
    await avecAttente("Lecture de la référence…", async () => {
      await televerser("ref", fichiers[0]);
      const r = await api("/api/ouvrir", { role: "ref" });
      etat.geo.ref = r.geometrie;
      $("#nc-depot").classList.add("charge");
      $("#nc-depot .etat").textContent = `${r.nom} — ${r.nb_entites} objets`;
      etat.refNouveauChantier = true; majBoutonCreer();
      montrerGeo([{ geo: r.geometrie, couleur: () => "#3a3f47" }], "Référence du nouveau chantier", r.geometrie.emprise);
    });
  } catch { /* déjà signalé */ }
});
$("#nc-creer").addEventListener("click", async () => {
  try {
    const r = await avecAttente("Création du chantier…", () => api("/api/chantier/creer", { nom: $("#nc-nom").value }));
    await ouvrirChantier(r.chantier);
    toast("Chantier créé. Ajoutez maintenant un premier plan à caler.", true);
  } catch { /* déjà signalé */ }
});

// =========================================================================== UN CHANTIER
async function ouvrirChantier(nom, cocherDerniers = true) {
  etat.chantier = nom;
  aller("chantier");
  try { await chargerChantiers(); } catch (e) { toast(e.message); return; }
  const ch = chantierCourant();
  if (!ch) { ouvrirAccueil(); return; }
  if (cocherDerniers) etat.coches = new Set(ch.types.filter((t) => t.indices.length).map((t) => t.indices[0].id));
  afficherChantier();
  majVueChantier();
}

function afficherChantier() {
  const ch = chantierCourant();
  $("#ch-nom").textContent = ch.nom;
  $("#ch-ref").innerHTML = `Référence MN95 : <b>${esc(ch.reference || "aucune")}</b>
    <button class="lien-discret" id="ch-changer-ref">changer</button>`;
  $("#ch-changer-ref").addEventListener("click", () => $("#ch-nouvelle-ref").click());
  $("#cartes-plans").innerHTML = ch.types.length ? ch.types.map((t, k) => {
    const d = t.indices[0];
    const badge = (m) => m && m !== "identique" ? `<span class="badge ${m === "recalé" ? "recale" : "force"}">${esc(m)}</span>` : "";
    const lignes = t.indices.map((i) => `<div class="ligne">
        <input type="checkbox" class="coche-indice" value="${esc(i.id)}" ${etat.coches.has(i.id) ? "checked" : ""}>
        <b>${esc(i.indice || i.plan)}</b> <small>${esc(dateCourte(i.date))}</small>${badge(i.mode)}
        <span class="droite"><a href="/telecharger_indice?id=${encodeURIComponent(i.id)}">DXF</a>
        <button class="lien-discret suppr-indice" data-id="${esc(i.id)}" data-nom="${esc(i.indice || i.plan)}" data-type="${esc(t.type)}" title="Supprimer cet indice">supprimer</button></span></div>`).join("");
    return `<div class="carte carte-plan">
      <span class="nom-plan"><b>${esc(t.type)}</b></span>
      <small>${d ? `Dernier indice : <b>${esc(d.indice || d.plan)}</b> du ${esc(dateCourte(d.date))}` : "Aucun indice"}${d ? badge(d.mode) : ""}</small>
      <div class="actions">
        <button class="nouvel-indice" data-type="${esc(t.type)}">Nouvel indice reçu</button>
        ${d ? `<a class="bouton" href="/telecharger_indice?id=${encodeURIComponent(d.id)}">Télécharger le DXF</a>` : ""}
      </div>
      <details class="historique" ${t.indices.length > 1 ? "" : ""}><summary>Historique (${t.indices.length} indice${t.indices.length > 1 ? "s" : ""})</summary>${lignes}</details>
      <button class="lien-discret suppr-plan" data-type="${esc(t.type)}">Supprimer ce plan</button>
    </div>`;
  }).join("") : `<p class="vide">Aucun plan calé sur ce chantier. Cliquez « Ajouter un plan à caler ».</p>`;
  $$(".nouvel-indice").forEach((b) => b.addEventListener("click", () => ouvrirIndice(b.dataset.type)));
  $$(".coche-indice").forEach((c) => c.addEventListener("change", () => {
    c.checked ? etat.coches.add(c.value) : etat.coches.delete(c.value);
    majVueChantier();
  }));
  $$(".suppr-indice").forEach((b) => b.addEventListener("click", () => supprimer(
    `Supprimer l'indice « ${b.dataset.nom} » du plan « ${b.dataset.type} » ?`, { id: b.dataset.id })));
  $$(".suppr-plan").forEach((b) => b.addEventListener("click", () => supprimer(
    `Supprimer le plan « ${b.dataset.type} » avec sa recette et TOUS ses indices ?`, { type_plan: b.dataset.type })));
  $("#btn-differences").disabled = etat.coches.size !== 2;
}

async function supprimer(question, quoi) {
  if (!confirm(question + "\n\n(Rien n'est effacé définitivement : l'élément part dans la corbeille du dossier CalagePlans.)")) return;
  try {
    await api("/api/supprimer", { chantier: etat.chantier, ...quoi });
    toast("Supprimé (placé dans la corbeille).", true);
    if (!quoi.id && !quoi.type_plan) ouvrirAccueil();
    else { if (quoi.id) etat.coches.delete(quoi.id); ouvrirChantier(etat.chantier, false); }
  } catch (e) { toast(e.message); }
}
$("#ch-supprimer").addEventListener("click", (ev) => {
  ev.preventDefault();
  supprimer(`Supprimer TOUT le chantier « ${etat.chantier} » (référence, plans, indices) ?`, {});
});
$("#ch-nouvelle-ref").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; ev.target.value = "";
  if (!f || !confirm(`Remplacer la référence du chantier par « ${f.name} » ?`)) return;
  try {
    await avecAttente("Lecture de la nouvelle référence…", async () => {
      await televerser("ref", f);
      await api("/api/chantier/reference", { chantier: etat.chantier });
    });
    toast("Référence remplacée.", true);
    ouvrirChantier(etat.chantier, false);
  } catch { /* déjà signalé */ }
});
$("#btn-ajouter-plan").addEventListener("click", ouvrirAjout);

// Affichage : référence + indices cochés, chacun dans sa couleur
async function majVueChantier() {
  $("#btn-differences").disabled = etat.coches.size !== 2;
  const ids = [...etat.coches];
  try {
    const r = await api("/api/comparer", { ids, mode: "superposer", chantier: etat.chantier });
    const couches = [];
    let leg = "";
    if (r.reference) { couches.push({ geo: r.reference, couleur: () => "#c4c8cf", textes: false }); leg += `<div><i style="background:#c4c8cf"></i> Référence</div>`; }
    r.calques.forEach((c, k) => {
      const coul = PALETTE[k % PALETTE.length];
      couches.push({ geo: c.geometrie, couleur: () => coul });
      leg += `<div><i style="background:${coul}"></i> ${esc(c.nom)}</div>`;
    });
    $("#legende").innerHTML = leg;
    if (!couches.length) { afficherVues("accueil", "Ce chantier n'a pas encore de référence."); return; }
    const emp = r.calques.length ? r.calques[0].geometrie.emprise : r.reference.emprise;
    montrerGeo(couches, r.calques.length ? "Plans calés sur la référence" : "Référence du chantier", emp);
  } catch (e) { toast(e.message); }
}

function montrerGeo(couches, titre, emprise) {
  vUnique.vider();
  vUnique.origine = couches[0].geo.origine;
  couches.forEach((c) => vUnique.ajouterCouche(c));
  $("#titre-unique").textContent = titre;
  $("#case-couleurs").hidden = true;
  afficherVues("unique");
  requestAnimationFrame(() => vUnique.ajuster(emprise));
}

$("#btn-differences").addEventListener("click", async () => {
  const ids = [...etat.coches].sort((a, b) => a.split("/")[2].localeCompare(b.split("/")[2]));   // plus ancien d'abord
  try {
    const r = await avecAttente("Comparaison des deux indices…", () => api("/api/comparer", { ids, mode: "differences" }));
    const couches = [];
    if (r.reference) couches.push({ geo: r.reference, couleur: () => "#dadde2", textes: false });
    if (r.inchanges) couches.push({ geo: r.inchanges, couleur: () => "#7b818c" });
    if (r.supprimes) couches.push({ geo: r.supprimes, couleur: () => "#d62020", epaisseur: 2 });
    if (r.ajoutes) couches.push({ geo: r.ajoutes, couleur: () => "#12a33a", epaisseur: 2 });
    const lignes = Object.entries(r.par_calque).slice(0, 15).map(([c, v]) =>
      `<tr><td>${esc(c)}</td><td style="color:#12a33a">+${v.ajoutes}</td><td style="color:#d62020">−${v.supprimes}</td></tr>`).join("");
    $("#legende").innerHTML = `<p><b>Ce qui a changé entre ${esc(r.ancien)} et ${esc(r.nouveau)}</b></p>
      <div><i style="background:#12a33a"></i> Nouveau ou déplacé : ${r.compte.ajoutes}</div>
      <div><i style="background:#d62020"></i> Supprimé ou déplacé : ${r.compte.supprimes}</div>
      <div><i style="background:#7b818c"></i> Inchangé : ${r.compte.inchanges}</div>
      ${lignes ? `<table>${lignes}</table>` : ""}
      <button class="lien-discret" id="retour-superposition">revenir à l'affichage normal</button>`;
    $("#retour-superposition").addEventListener("click", majVueChantier);
    montrerGeo(couches, `Différences ${r.ancien} → ${r.nouveau}`, (r.inchanges || r.ajoutes || r.supprimes).emprise);
  } catch { /* déjà signalé */ }
});

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
const televerser = (role, f) => api(`/api/fichier?role=${role}&nom=${encodeURIComponent(f.name)}`, f);
function viderDepot(label) { label.classList.remove("charge"); label.querySelector(".etat").textContent = ""; }

// =========================================================================== AJOUTER UN PLAN
async function ouvrirAjout() {
  aller("ajout");
  // remise à zéro de l'assistant
  etat.geo.plan = null; etat.infos = {}; etat.familles = []; etat.zone = null; etat.historique = [];
  etat.selection = null; etat.dessinZone = false;
  reinitialiserCalage();
  $("#type-plan").value = ""; $("#indice").value = "A";
  viderDepot($('#e-ajout .depot[data-role="plan"]')); viderDepot($('#e-ajout .depot[data-role="xref"]'));
  $("#alertes-fichiers").innerHTML = ""; $("#resultat-export").innerHTML = "";
  $("#vers-calage").disabled = true;
  $("#effacer-zone").disabled = true; $("#dessiner-zone").textContent = "Dessiner la zone"; $("#dessiner-zone").classList.remove("actif");
  majTypesProposes();
  ouvrirEtape(1);
  try {   // la référence du chantier
    const r = await avecAttente("Lecture de la référence du chantier…", () => api("/api/reference_chantier", { chantier: etat.chantier }));
    etat.geo.ref = r.geometrie; etat.infos.ref = r;
  } catch { return; }
  preparerVuesDoubles();
  montrerCoteACote();
  requestAnimationFrame(() => vRef.ajuster(etat.geo.ref.emprise));
  $("#type-plan").focus();
}

function majTypesProposes() {
  const ch = chantierCourant();
  const existants = ch ? ch.types.map((t) => t.type) : [];
  $("#liste-types").innerHTML = etat.typesExistants.filter((t) => !existants.includes(t)).map((t) => `<option value="${esc(t)}">`).join("");
  const t = $("#type-plan").value.trim().toLowerCase();
  $("#avert-type").innerHTML = existants.some((x) => x.toLowerCase() === t)
    ? "Ce plan existe déjà sur ce chantier : il sera <b>recalé</b> et sa recette remplacée. Pour une simple nouvelle version, utilisez plutôt « Nouvel indice reçu »."
    : "";
  majBoutonContinuer();
}
function majBoutonContinuer() { $("#vers-calage").disabled = !($("#type-plan").value.trim() && etat.geo.plan); }
$("#type-plan").addEventListener("input", majTypesProposes);

function ouvrirEtape(n) {
  $$("#e-ajout .etape").forEach((e) => {
    const k = +e.dataset.etape;
    e.classList.toggle("ouverte", k === n); e.classList.toggle("faite", k < n);
  });
  etat.etape = n;
  $("#info-objet").hidden = true;
}
$$("#e-ajout .etape > h2").forEach((h) => h.addEventListener("click", () => {
  const e = h.parentElement, n = +e.dataset.etape;
  if (!e.classList.contains("faite") || etat.etape === 4) return;
  ouvrirEtape(n);
  if (n <= 2) montrerCoteACote(); else if (n === 3) montrerSuperposition();
}));

$$("#e-ajout .depot").forEach((label) => installerDepot(label, async (fichiers) => {
  const role = label.dataset.role;
  try {
    await avecAttente("Lecture du plan (conversion, liaison des xrefs, explosion des blocs)…", async () => {
      if (role === "xref") {
        for (const f of fichiers) await televerser("xref", f);
        label.querySelector(".etat").textContent = fichiers.map((f) => f.name).join(", ");
        label.classList.add("charge");
        if (etat.geo.plan) apresOuverturePlan(await api("/api/ouvrir", { role: "plan" }));
      } else {
        await televerser("plan", fichiers[0]);
        apresOuverturePlan(await api("/api/ouvrir", { role: "plan" }));
      }
    });
  } catch { /* déjà signalé */ }
}));

function apresOuverturePlan(r) {
  etat.geo.plan = r.geometrie; etat.infos.plan = r;
  const label = $('#e-ajout .depot[data-role="plan"]');
  label.classList.add("charge");
  label.querySelector(".etat").textContent = `${r.nom} — ${r.nb_entites} objets, unité : ${r.unite}`;
  reinitialiserCalage();
  // calques gelés ou éteints dans le DWG : décochés d'office
  etat.gardes = new Set(r.geometrie.calques.filter((c) => !c.etat).map((c) => c.nom));
  etat.zone = null; etat.historique = []; etat.familles = [];
  const base = r.nom.replace(/\.[^.]+$/, "");
  $("#prefixe").value = `DWG_${base} - `;
  if (!$("#type-plan").value.trim()) $("#type-plan").value = "";
  const al = [];
  for (const a of r.alertes) al.push(`<div class="alerte">${esc(a)}</div>`);
  for (const x of r.xrefs) if (!x.statut.startsWith("VIDE"))
    al.push(`<div class="info">Xref « ${esc(x.nom)} » : ${esc(x.statut)} (${x.nb_entites} objets)</div>`);
  const geles = r.geometrie.calques.filter((c) => c.etat).length;
  if (geles) al.push(`<div class="info">${geles} calque(s) gelé(s) ou éteint(s) dans le fichier : ils seront écartés (modifiable au nettoyage).</div>`);
  $("#alertes-fichiers").innerHTML = al.join("");
  majBoutonContinuer();
  preparerVuesDoubles();
  montrerCoteACote();
  requestAnimationFrame(() => vPlan.ajuster(etat.geo.plan.emprise));
}

$("#vers-calage").addEventListener("click", () => { ouvrirEtape(2); afficherElements(); montrerCoteACote(); remplirChoixAuto(); });

// --------------------------------------------------------------------------- calage
function reinitialiserCalage() {
  etat.elements = [
    { type: "point", role: "calage", plan: null, ref: null },
    { type: "point", role: "calage", plan: null, ref: null },
    { type: "point", role: "controle", plan: null, ref: null },
  ];
  etat.calage = null; etat.sim = null; etat.auto = false;
  $("#resultat-calage").innerHTML = ""; $("#bandeau-auto").innerHTML = "";
  $("#vers-nettoyage").disabled = true; $("#voir-superposition").disabled = true;
}
function preparerVuesDoubles() {
  for (const [v, role] of [[vPlan, "plan"], [vRef, "ref"]]) {
    const geo = etat.geo[role];
    v.vider();
    if (!geo) continue;
    v.origine = geo.origine;
    v.ajouterCouche({ geo, couleur: role === "ref" ? () => "#3a3f47" : (g) => geo.calques[g.l].couleur });
  }
  majMarques();
}
function montrerCoteACote() {
  etat.vueSuperposee = false;
  $("#voir-cote-a-cote").hidden = true; $("#voir-superposition").hidden = false;
  afficherVues("double");
}
const nomRole = (el) => (el.role === "controle" ? "vérification" : "calage");
const etiquette = (i) => {
  const el = etat.elements[i];
  return el.role === "controle" ? `${i + 1} vérif.` : `${i + 1}${el.type === "axe" ? " axe" : ""}`;
};
function majMarques() {
  vPlan.marques = []; vRef.marques = []; vPlan.traits = []; vRef.traits = [];
  etat.elements.forEach((el, i) => {
    const coul = el.role === "controle" ? COUL_VERIF : COUL_CAL;
    for (const [v, cote] of [[vPlan, "plan"], [vRef, "ref"]]) {
      const pt = el[cote];
      if (!pt) continue;
      if (pt.segment) v.traits.push({ s: pt.segment, texte: etiquette(i), couleur: coul });
      else v.marques.push({ x: pt.x, y: pt.y, texte: etiquette(i), couleur: coul });
    }
  });
  vPlan.dessiner(); vRef.dessiner();
}
function afficherElements() {
  const ol = $("#liste-elements");
  const prochain = etat.elements.findIndex((e) => !e.plan || !e.ref);
  const ecarts = etat.calage ? etat.calage.elements : null;
  let iComplet = 0;
  ol.innerHTML = etat.elements.map((el, i) => {
    const puce = (pt, cote) => pt ? `<span class="puce ${pt.genre === "libre" ? "libre" : "ok"}" title="${esc(pt.genre)}">${cote} ✓</span>` : `<span class="puce">${cote}</span>`;
    let ecart = "";
    if (el.plan && el.ref && ecarts) {
      const e = ecarts[iComplet++];
      if (e) ecart = `<span class="ecart">${el.role === "controle" ? "tombe à" : "écart"} ${fmt(e.ecart_m * 1000, 0)} mm${e.ecart_angle_deg != null ? `, angle ${fmt(e.ecart_angle_deg, 3)}°` : ""}</span>`;
    }
    return `<li class="${i === prochain ? "courant" : ""}"><span class="idx">${i + 1}</span>
      <select data-type="${i}"><option value="point" ${el.type === "point" ? "selected" : ""}>Point</option><option value="axe" ${el.type === "axe" ? "selected" : ""}>Axe</option></select>
      <span><button class="role ${el.role}" data-role="${i}" title="Cliquer pour passer de calage à vérification">${nomRole(el)}</button></span>
      ${puce(el.plan, "plan")}${puce(el.ref, "réf.")}<button class="effacer" data-effacer="${i}" title="Effacer (ou supprimer la ligne si vide)">✕</button>${ecart}</li>`;
  }).join("");
  ol.querySelectorAll("[data-type]").forEach((s) => s.addEventListener("change", () => {
    const el = etat.elements[+s.dataset.type]; el.type = s.value; el.plan = null; el.ref = null; apresModif();
  }));
  ol.querySelectorAll("[data-role]").forEach((b) => b.addEventListener("click", () => {
    const el = etat.elements[+b.dataset.role]; el.role = el.role === "controle" ? "calage" : "controle"; apresModif();
  }));
  ol.querySelectorAll("[data-effacer]").forEach((b) => b.addEventListener("click", () => {
    const i = +b.dataset.effacer, el = etat.elements[i];
    if (el.plan || el.ref) { el.plan = null; el.ref = null; } else if (etat.elements.length > 1) etat.elements.splice(i, 1);
    apresModif();
  }));
}
function apresModif() { afficherElements(); majMarques(); calculerCalage(); }
function ajouterElement(type, role) {
  // insérée avant les vérifications pour garder la liste lisible
  const el = { type, role, plan: null, ref: null };
  if (role === "calage") {
    const i = etat.elements.findIndex((e) => e.role === "controle");
    i < 0 ? etat.elements.push(el) : etat.elements.splice(i, 0, el);
  } else etat.elements.push(el);
  apresModif();
}
$("#ajout-point").addEventListener("click", () => ajouterElement("point", etat.auto ? "controle" : "calage"));
$("#ajout-axe").addEventListener("click", () => ajouterElement("axe", etat.auto ? "controle" : "calage"));
$("#ajout-verif").addEventListener("click", () => ajouterElement("point", "controle"));

const survolEnCours = { plan: false, ref: false }, survolSuivant = { plan: null, ref: null };
function elementCourant(cote) { return etat.elements.find((e) => !e[cote]); }
async function demanderSurvol(cote, vue, x, y) {
  if (etat.ecran !== "ajout" || etat.etape !== 2) { vue.survol = null; return; }
  const el = elementCourant(cote);
  if (!el) { if (vue.survol) { vue.survol = null; vue.dessiner(); } return; }
  if (survolEnCours[cote]) { survolSuivant[cote] = [x, y]; return; }
  survolEnCours[cote] = true;
  try {
    const r = await api("/api/accroche", { role: cote, x, y, rayon: 14 / vue.k, mode: el.type });
    vue.survol = r.aucun ? null : (r.segment ? { segment: r.segment } : r);
    vue.dessiner();
  } catch { /* silencieux au survol */ }
  survolEnCours[cote] = false;
  if (survolSuivant[cote]) { const [a, b] = survolSuivant[cote]; survolSuivant[cote] = null; demanderSurvol(cote, vue, a, b); }
}
vPlan.surSurvol = (x, y) => demanderSurvol("plan", vPlan, x, y);
vRef.surSurvol = (x, y) => demanderSurvol("ref", vRef, x, y);
vPlan.surSortie = () => { vPlan.survol = null; vPlan.dessiner(); };
vRef.surSortie = () => { vRef.survol = null; vRef.dessiner(); };

async function cliquerElement(cote, vue, x, y) {
  if (etat.ecran !== "ajout" || etat.etape !== 2) return;
  const el = elementCourant(cote);
  if (!el) { toast("Tous les éléments sont saisis sur ce plan : ajoutez une ligne (+ Point / + Axe / + Vérification) ou effacez-en un.", true); return; }
  let pt;
  try {
    const r = await api("/api/accroche", { role: cote, x, y, rayon: 14 / vue.k, mode: el.type });
    if (el.type === "axe") {
      if (r.aucun) { toast("Aucune ligne sous le curseur : cliquez sur un trait droit.", true); return; }
      pt = { segment: r.segment, genre: "axe" };
    } else if (r.aucun) {
      pt = { x, y, genre: "libre" };
      toast("Aucun coin ni croisement sous le curseur : point pris tel quel (zoomez pour plus de précision).", true);
    } else pt = r;
  } catch (e) { toast(e.message); return; }
  el[cote] = pt;
  vue.survol = null;
  apresModif();
}
vPlan.surClic = (x, y) => cliquerElement("plan", vPlan, x, y);
vRef.surClic = (x, y) => cliquerElement("ref", vRef, x, y);

function elementsPourServeur() {
  return etat.elements.filter((e) => e.plan && e.ref).map((e) => ({
    type: e.type, role: e.role, plan: e.plan.segment || [e.plan.x, e.plan.y], ref: e.ref.segment || [e.ref.x, e.ref.y],
  }));
}
async function calculerCalage() {
  const els = elementsPourServeur();
  const nbCal = els.filter((e) => e.role === "calage").length;
  if (!etat.auto && nbCal < 2) {
    etat.calage = null; etat.sim = null;
    $("#resultat-calage").innerHTML = nbCal ? `<p class="aide">Il faut au moins 2 éléments de calage.</p>` : "";
    $("#vers-nettoyage").disabled = true; $("#voir-superposition").disabled = true;
    return;
  }
  try {
    afficherResultatCalage(await api("/api/calage", { elements: els, auto: etat.auto }));
  } catch (e) {
    etat.calage = null; etat.sim = null;
    $("#resultat-calage").innerHTML = `<div class="verdict mauvais">${esc(e.message)}</div>`;
    $("#vers-nettoyage").disabled = true; $("#voir-superposition").disabled = true;
  }
}
function afficherResultatCalage(r) {
  etat.calage = r; etat.sim = simDepuisCalage(r);
  const cls = { "bon": "bon", "à vérifier": "averifier", "mauvais": "mauvais", "sans contrôle": "sanscontrole" }[r.verdict];
  const ech = r.echelle_arrondie ? `${r.echelle} <small>(mesurée ${Number(r.echelle_brute).toPrecision(6)})</small>` : Number(r.echelle_brute).toPrecision(6);
  $("#resultat-calage").innerHTML = `<div class="verdict ${cls}"><b>${esc(r.message)}</b>
    <table><tr><td>Échelle</td><td>${ech}</td></tr>
      <tr><td>Rotation</td><td>${fmt(r.rotation_deg, 4)}°</td></tr>
      <tr><td>Translation</td><td>E ${fmt(r.translation[0])} / N ${fmt(r.translation[1])}</td></tr></table></div>`;
  afficherElements();
  $("#vers-nettoyage").disabled = false; $("#voir-superposition").disabled = false;
  if (etat.vueSuperposee) montrerSuperposition();
}

function remplirChoixAuto() {
  const ch = chantierCourant();
  let h = `<label class="case"><input type="checkbox" id="auto-ref" checked> Référence MN95</label>`;
  const avecIndices = ch ? ch.types.filter((t) => t.indices.length) : [];
  for (const t of avecIndices) {
    h += `<div class="type">${esc(t.type)}</div>` + t.indices.map((i, k) =>
      `<label class="case"><input type="checkbox" class="auto-id" value="${esc(i.id)}" ${k === 0 ? "checked" : ""}>
        ${esc(i.indice || i.plan)} <small>${esc(dateCourte(i.date))}</small></label>`).join("");
  }
  h += avecIndices.length ? `<p class="aide">Par défaut : le dernier indice de chaque plan.</p>`
    : `<p class="aide">Aucun autre plan calé sur ce chantier : comparaison avec la référence seule (souvent insuffisant).</p>`;
  $("#liste-auto").innerHTML = h;
}
$("#calage-auto").addEventListener("click", async () => {
  try {
    const r = await avecAttente("Recherche des éléments communs…", () => api("/api/calage_auto", {
      chantier: etat.chantier, ids: $$("#liste-auto .auto-id:checked").map((c) => c.value),
      avec_reference: $("#auto-ref") ? $("#auto-ref").checked : true,
    }));
    etat.auto = true;
    const c = r.confiance;
    const cls = { forte: "bon", moyenne: "averifier", faible: "mauvais" }[c.niveau];
    $("#bandeau-auto").innerHTML = `<div class="verdict ${cls}"><b>Calage automatique — confiance ${c.niveau.toUpperCase()}</b>
      <table><tr><td>Comparé avec</td><td>${esc(c.compare_avec.join(", "))}</td></tr>
      <tr><td>Points communs</td><td>${c.points_concordants} (écart moyen ${fmt(c.ecart_moyen_mm, 1)} mm)</td></tr></table>
      <p class="aide">Pour être sûr : cliquez un point de <b>vérification</b> sur les deux plans.</p>
      <button id="quitter-auto">Revenir au calage à la main</button></div>`;
    $("#quitter-auto").addEventListener("click", () => { etat.auto = false; $("#bandeau-auto").innerHTML = ""; reinitialiserCalage(); afficherElements(); majMarques(); });
    etat.elements = [{ type: "point", role: "controle", plan: null, ref: null }];
    afficherResultatCalage(r);
    majMarques();
    if (c.niveau !== "faible") montrerSuperposition(); else toast("Calage automatique incertain : calez à la main.");
  } catch { /* déjà signalé */ }
});

function montrerSuperposition() {
  if (!etat.sim) return;
  etat.vueSuperposee = true;
  vUnique.vider();
  vUnique.origine = etat.geo.ref.origine;
  vUnique.ajouterCouche({ geo: etat.geo.ref, couleur: () => COUL_REF, textes: false });
  vUnique.ajouterCouche({ geo: etat.geo.plan, sim: etat.sim, couleur: couleurPlanSuperpose, epaisseurDe: (g) => (g.l === etat.surligne || g.l === etat.selection ? 2.5 : 1) });
  if (etat.zone) vUnique.zone = etat.zone;
  $("#titre-unique").textContent = etat.etape >= 3 ? "Nettoyage — plan calé sur la référence (gris)" : "Plan calé sur la référence (gris)";
  $("#case-couleurs").hidden = false;
  $("#voir-cote-a-cote").hidden = etat.etape !== 2; $("#voir-superposition").hidden = true;
  afficherVues("unique");
  requestAnimationFrame(() => vUnique.ajuster(vUnique.empriseCouche(vUnique.couches[1])));
}
$("#voir-superposition").addEventListener("click", montrerSuperposition);
$("#voir-cote-a-cote").addEventListener("click", montrerCoteACote);

$("#vers-nettoyage").addEventListener("click", async () => {
  const r = etat.calage;
  if (r.verdict === "mauvais" && !confirm("La vérification indique un calage FAUX. Continuer quand même ?")) return;
  if (r.verdict === "sans contrôle" && !confirm("Pas de point de vérification : vous n'êtes pas sûr que le calage soit juste. Continuer quand même ?")) return;
  ouvrirEtape(3);
  await preparerNettoyage();
  montrerSuperposition();
});

// --------------------------------------------------------------------------- nettoyage
function typesSupprimes() {
  const s = new Set();
  for (const f of etat.familles) if (f.coche) f.types.forEach((t) => s.add(t));
  return s;
}
const modeHachures = () => ($('input[name="hachures"]:checked') || { value: "garder" }).value;
$$('input[name="hachures"]').forEach((r) => r.addEventListener("change", () => vUnique.dessiner()));
function couleurPlanSuperpose(g) {
  const geo = etat.geo.plan, nom = geo.calques[g.l].nom;
  if (etat.etape >= 3) {
    if (typesSupprimes().has(geo.types[g.t])) return null;
    if (geo.types[g.t] === "HATCH" && modeHachures() === "supprimer") return null;
    if (!etat.gardes.has(nom)) return $("#voir-ecartes").checked ? "#e4e6ea" : null;
  }
  if (g.l === etat.selection) return "#ff8a00";
  if (g.l === etat.surligne) return "#f5b342";
  return $("#couleurs-calques").checked ? geo.calques[g.l].couleur : COUL_ROUGE;
}
$("#couleurs-calques").addEventListener("change", () => vUnique.dessiner());
$("#voir-ecartes").addEventListener("change", () => vUnique.dessiner());

async function preparerNettoyage() {
  if (!etat.familles.length) {
    const r = await api("/api/familles");
    etat.familles = r.familles;
    $("#bloc-hachures").hidden = !r.nb_hachures;
    $("#nb-hachures").textContent = r.nb_hachures ? `(${r.nb_hachures})` : "";
  }
  $("#familles").innerHTML = etat.familles.map((f, i) =>
    `<label><input type="checkbox" data-famille="${i}" ${f.coche ? "checked" : ""}> ${esc(f.libelle)} <small>(${f.n})</small></label>`).join("");
  $$("#familles input").forEach((c) => c.addEventListener("change", () => { etat.familles[+c.dataset.famille].coche = c.checked; vUnique.dessiner(); }));
  const geles = etat.geo.plan.calques.filter((c) => c.etat).length;
  $("#info-geles").textContent = geles ? `Les ${geles} calque(s) gelé(s) ou éteint(s) dans le fichier sont écartés d'office.` : "";
  afficherCalques();
}
function majCompte() { $("#compte-calques").textContent = `(${etat.gardes.size} gardés sur ${etat.geo.plan.calques.length})`; }
function afficherCalques() {
  const geo = etat.geo.plan, filtre = $("#filtre-calques").value.toLowerCase();
  $("#liste-calques").innerHTML = geo.calques.map((c, i) => {
    if (filtre && !c.nom.toLowerCase().includes(filtre)) return "";
    const g = etat.gardes.has(c.nom);
    const detail = Object.entries(c.types).map(([t, n]) => `${t} ${n}`).join(", ");
    return `<li data-i="${i}" class="${g ? "" : "ecarte"} ${i === etat.selection ? "surligne" : ""}" title="${esc(detail)}">
      <input type="checkbox" ${g ? "checked" : ""}><span class="pastille" style="background:${c.couleur}"></span>
      <span class="nom">${esc(c.nom)}</span>${c.etat ? `<span class="badge gele">${esc(c.etat)}</span>` : ""}<span class="n">${c.n}</span></li>`;
  }).join("");
  majCompte();
  $$("#liste-calques li").forEach((li) => {
    const i = +li.dataset.i, nom = geo.calques[i].nom;
    li.querySelector("input").addEventListener("change", (e) => basculerCalque(nom, e.target.checked));
    li.addEventListener("mouseenter", () => { etat.surligne = i; vUnique.dessiner(); });
    li.addEventListener("mouseleave", () => { etat.surligne = null; vUnique.dessiner(); });
  });
  $("#annuler").disabled = !etat.historique.length;
}
function basculerCalque(nom, garder, memoriser = true) {
  if (etat.gardes.has(nom) === garder) return;
  if (memoriser) etat.historique.push({ nom, garder: !garder });
  garder ? etat.gardes.add(nom) : etat.gardes.delete(nom);
  afficherCalques(); vUnique.dessiner();
}
$("#annuler").addEventListener("click", () => { const h = etat.historique.pop(); if (h) basculerCalque(h.nom, h.garder, false); });
$("#filtre-calques").addEventListener("input", afficherCalques);
$("#tout-cocher").addEventListener("click", () => { etat.geo.plan.calques.forEach((c) => etat.gardes.add(c.nom)); etat.historique = []; afficherCalques(); vUnique.dessiner(); });
$("#tout-decocher").addEventListener("click", () => { etat.gardes.clear(); etat.historique = []; afficherCalques(); vUnique.dessiner(); });
$("#mode-ecarter").addEventListener("click", () => {
  etat.modeEcarter = !etat.modeEcarter;
  $("#mode-ecarter").classList.toggle("actif", etat.modeEcarter);
  $("#mode-ecarter").textContent = etat.modeEcarter ? "Écarter au clic : ACTIF" : "Écarter au clic";
});

function objetSous(x, y, inclureEcartes) {
  const geo = etat.geo.plan, s = etat.sim;
  const [px, py] = inverserSim(s, x, y);
  const rx = px - geo.origine[0], ry = py - geo.origine[1];
  let dmin = 9 / (vUnique.k * Math.hypot(s.ar, s.ai)), meilleur = null;
  for (const g of geo.groupes) {
    if (!couleurPlanSuperpose(g)) continue;
    if (!inclureEcartes && !etat.gardes.has(geo.calques[g.l].nom)) continue;
    for (const l of g.lignes) {
      for (let i = 0; i + 3 < l.length; i += 2) {
        const d = distSeg(rx, ry, l[i], l[i + 1], l[i + 2], l[i + 3]);
        if (d < dmin) { dmin = d; meilleur = g; }
      }
    }
  }
  return meilleur;
}
const enNettoyage = () => etat.ecran === "ajout" && etat.etape === 3;
let survolNettoyagePrevu = false;
vUnique.surSurvol = (x, y, e) => {
  if (!enNettoyage() || etat.dessinZone) { $("#bulle").hidden = true; return; }
  if (survolNettoyagePrevu) return;
  survolNettoyagePrevu = true;
  requestAnimationFrame(() => {
    survolNettoyagePrevu = false;
    const g = objetSous(x, y, $("#voir-ecartes").checked);
    const bulle = $("#bulle"), avant = etat.surligne;
    etat.surligne = g ? g.l : null;
    if (g) {
      const nom = etat.geo.plan.calques[g.l].nom, garde = etat.gardes.has(nom);
      bulle.textContent = `${nom}${garde ? "" : " (écarté — clic : garder)"}${etat.modeEcarter && garde ? " — clic : écarter" : ""}`;
      bulle.style.left = `${e.offsetX + 14}px`; bulle.style.top = `${e.offsetY + 14}px`;
      bulle.hidden = false;
    } else bulle.hidden = true;
    if (avant !== etat.surligne) vUnique.dessiner();
  });
};
vUnique.surSortie = () => { $("#bulle").hidden = true; if (etat.surligne !== null) { etat.surligne = null; vUnique.dessiner(); } };
vUnique.surClic = (x, y) => {
  if (!enNettoyage()) return;
  if (etat.dessinZone) { etat.zone.pts.push([x, y]); vUnique.dessiner(); return; }
  const g = objetSous(x, y, $("#voir-ecartes").checked);
  const boite = $("#info-objet");
  if (!g) { boite.hidden = true; etat.selection = null; afficherCalques(); vUnique.dessiner(); return; }
  const c = etat.geo.plan.calques[g.l], garde = etat.gardes.has(c.nom);
  if (etat.modeEcarter && garde) { basculerCalque(c.nom, false); toast(`Calque « ${c.nom} » écarté.`, true); return; }
  if (!garde) { basculerCalque(c.nom, true); toast(`Calque « ${c.nom} » de nouveau gardé.`, true); return; }
  etat.selection = g.l;
  boite.innerHTML = `Calque <b>« ${esc(c.nom)} »</b> sélectionné — ${c.n} objets <button id="ecarter-selection">Écarter ce calque</button>`;
  boite.hidden = false;
  $("#ecarter-selection").addEventListener("click", () => { etat.selection = null; boite.hidden = true; basculerCalque(c.nom, false); });
  afficherCalques(); vUnique.dessiner();
  const li = $(`#liste-calques li[data-i="${g.l}"]`);
  if (li) li.scrollIntoView({ block: "nearest" });
};
document.addEventListener("keydown", (e) => {
  if (!enNettoyage()) return;
  if ((e.key === "Delete" || e.key === "Backspace") && etat.selection !== null && document.activeElement.tagName !== "INPUT") {
    const nom = etat.geo.plan.calques[etat.selection].nom;
    etat.selection = null; $("#info-objet").hidden = true; basculerCalque(nom, false);
  }
  if (e.key === "Escape") { etat.selection = null; $("#info-objet").hidden = true; vUnique.dessiner(); }
  if ((e.ctrlKey || e.metaKey) && e.key === "z") { $("#annuler").click(); e.preventDefault(); }
});

$("#dessiner-zone").addEventListener("click", () => {
  if (etat.dessinZone && etat.zone && etat.zone.pts.length >= 3) { fermerZone(); return; }   // « Terminer la zone »
  etat.dessinZone = !etat.dessinZone;
  if (etat.dessinZone) { etat.zone = { pts: [], fermee: false }; vUnique.zone = etat.zone; }
  $("#dessiner-zone").classList.toggle("actif", etat.dessinZone);
  $("#dessiner-zone").textContent = etat.dessinZone ? "Terminer la zone (ou double-clic)" : "Redessiner la zone";
  vUnique.dessiner();
});
$("#effacer-zone").addEventListener("click", () => {
  etat.zone = null; vUnique.zone = null; etat.dessinZone = false;
  $("#dessiner-zone").classList.remove("actif"); $("#dessiner-zone").textContent = "Dessiner la zone";
  $("#effacer-zone").disabled = true; vUnique.dessiner();
});
vUnique.surDoubleClic = () => { if (enNettoyage() && etat.dessinZone) fermerZone(); };
function fermerZone() {
  const z = etat.zone;
  while (z.pts.length > 1) {
    const a = z.pts[z.pts.length - 1], b = z.pts[z.pts.length - 2];
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) * vUnique.k < 6) z.pts.pop(); else break;
  }
  if (z.pts.length < 3) { toast("La zone doit avoir au moins 3 sommets.", true); return; }
  z.fermee = true; etat.dessinZone = false;
  $("#dessiner-zone").classList.remove("actif"); $("#dessiner-zone").textContent = "Redessiner la zone";
  $("#effacer-zone").disabled = false; vUnique.dessiner();
};

// --------------------------------------------------------------------------- enregistrer
$("#exporter").addEventListener("click", async () => {
  if (etat.dessinZone) { toast("Terminez d'abord la zone (double-clic) ou effacez-la.", true); return; }
  const corps = {
    chantier: etat.chantier, type_plan: $("#type-plan").value.trim(), indice: $("#indice").value.trim(),
    prefixe: $("#prefixe").value, calques: [...etat.gardes],
    familles: etat.familles.filter((f) => f.coche).map((f) => f.libelle),
    zone: etat.zone && etat.zone.fermee ? etat.zone.pts : null, hachures: modeHachures(),
  };
  try {
    const r = await avecAttente("Création du DXF calé et de la recette…", () => api("/api/exporter", corps));
    ouvrirEtape(4);
    afficherRapport($("#resultat-export"), r, "creation");
    const p = document.createElement("div");
    p.innerHTML = `<p class="aide">Le plan « ${esc(corps.type_plan)} » est enregistré. Ses prochaines versions : bouton
      <b>« Nouvel indice reçu »</b> sur la page du chantier.</p>
      <div class="boutons"><button class="principal" id="fin-retour">Retour au chantier</button>
      <button id="fin-autre">Ajouter un autre plan</button></div>`;
    $("#resultat-export").prepend(p);
    $("#fin-retour").addEventListener("click", () => ouvrirChantier(etat.chantier));
    $("#fin-autre").addEventListener("click", async () => { await chargerChantiers(); ouvrirAjout(); });
    montrerResultat(r);
    chargerChantiers();
  } catch { /* déjà signalé */ }
});

// =========================================================================== rapport commun
const NOMS_TYPES = { LINE: "lignes", LWPOLYLINE: "polylignes", POLYLINE: "polylignes (anciennes)", CIRCLE: "cercles",
  ARC: "arcs", TEXT: "textes", MTEXT: "textes multilignes", HATCH: "hachures", DIMENSION: "cotations",
  MULTILEADER: "lignes de repère", ATTDEF: "attributs", ATTRIB: "attributs", SOLID: "surfaces pleines",
  SPLINE: "courbes", ELLIPSE: "ellipses", MLINE: "multilignes", POINT: "points" };
const nomType = (t) => NOMS_TYPES[t] || t;

function afficherRapport(cible, rep, contexte) {
  const r = rep.rapport, mode = r.mode, creation = contexte === "creation";
  const titres = {
    "identique": creation ? "DXF calé créé" : "Même origine que l'indice précédent : calé sans problème",
    "recalé": "Calé AVEC CORRECTION AUTOMATIQUE : ce plan avait bougé par rapport à l'indice précédent",
    "forcé": "Produit SANS CONTRÔLE (forcé) : à vérifier soigneusement",
    "refus": "Refusé : aucun fichier produit",
  };
  const cls = { "identique": "identique", "recalé": "recale", "forcé": "force", "refus": "refus" }[mode];
  let h = `<div class="bandeau ${cls}">${titres[mode] || mode}`;
  if (mode !== "identique") h += `<p>${esc(r.controle.message || "")}</p>`;
  h += `</div>`;
  if (rep.telecharger) h += `<a class="telecharger" href="${rep.telecharger}">Télécharger le DXF calé</a>`;
  if (mode === "refus" && !creation) {
    h += `<p class="aide">Rien n'a été enregistré. Ce plan doit être recalé : retournez au chantier, « Ajouter un plan à
      caler » avec le même nom de plan (le calage automatique peut aider).</p>
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
    </table>`;
    if (rep.chemin_sortie) h += `<p class="aide">Enregistré dans : ${esc(rep.chemin_sortie)}</p>`;
  }
  h += `<details><summary>Rapport complet</summary><pre>${esc(rep.texte)}</pre></details>`;
  cible.innerHTML = h;
  const bf = cible.querySelector("#forcer");
  if (bf) bf.addEventListener("click", () => {
    if (confirm("Le contrôle a échoué : le fichier produit risque d'être FAUX. Produire quand même ?")) calerIndice(true);
  });
}
function montrerResultat(rep) {
  const couches = [];
  if (rep.reference) couches.push({ geo: rep.reference, couleur: () => COUL_REF, textes: false });
  if (rep.resultat) couches.push({ geo: rep.resultat, couleur: () => COUL_ROUGE });
  if (!couches.length) { afficherVues("accueil", "Aucun fichier produit."); return; }
  montrerGeo(couches, rep.resultat ? "Résultat : DXF calé (rouge) sur la référence (gris)" : "Référence du chantier (aucun fichier produit)",
    (rep.resultat || rep.reference).emprise);
}

// =========================================================================== NOUVEL INDICE
function indiceSuivant(dernier) {
  if (!dernier) return "";
  const m = dernier.match(/^(.*?)(\d+)$/);
  if (m) return m[1] + String(+m[2] + 1);
  if (/^[A-Ya-y]$/.test(dernier)) return String.fromCharCode(dernier.charCodeAt(0) + 1);
  return "";
}
const typeIndice = () => (chantierCourant() || { types: [] }).types.find((t) => t.type === etat.typeIndice);
async function ouvrirIndice(type) {
  etat.typeIndice = type;
  aller("indice");
  const t = typeIndice();
  $("#ni-titre").textContent = `Nouvel indice — ${type}`;
  $("#ni-historique").innerHTML = t && t.indices.length
    ? `Indices déjà enregistrés : ${t.indices.map((i) => `<b>${esc(i.indice || i.plan)}</b>`).reverse().join(" → ")}` : "";
  $("#i-indice").value = t ? indiceSuivant(t.indices.length ? t.indices[0].indice : "") : "";
  etat.indiceCharge = false;
  viderDepot($('#e-indice .depot[data-role="indice"]')); viderDepot($('#e-indice .depot[data-role="indice_xref"]'));
  $("#resultat-indice").innerHTML = "";
  majInfoIndice();
  // affichage : la référence et le dernier indice de ce plan
  if (t && t.indices.length) { etat.coches = new Set([t.indices[0].id]); majVueChantier(); }
}
function majInfoIndice() {
  const t = typeIndice(), nom = $("#i-indice").value.trim().toLowerCase();
  $("#i-avert-indice").textContent = t && nom && t.indices.some((i) => (i.indice || "").toLowerCase() === nom)
    ? "Attention : un indice porte déjà ce nom pour ce plan." : "";
  $("#caler-indice").disabled = !(t && etat.indiceCharge);
}
$("#i-indice").addEventListener("input", majInfoIndice);
$$("#e-indice .depot").forEach((label) => installerDepot(label, async (fichiers) => {
  const role = label.dataset.role;
  try {
    await avecAttente("Envoi du fichier…", async () => {
      if (role === "indice_xref") for (const f of fichiers) await televerser(role, f);
      else { await televerser(role, fichiers[0]); etat.indiceCharge = true; }
    });
    label.classList.add("charge");
    label.querySelector(".etat").textContent = fichiers.map((f) => f.name).join(", ");
    majInfoIndice();
  } catch { /* déjà signalé */ }
}));
async function calerIndice(forcer = false) {
  if (!$("#i-indice").value.trim() && !confirm("Aucun nom d'indice : continuer quand même ?")) return;
  try {
    const r = await avecAttente("Calage du nouvel indice (contrôle, nettoyage, transformation)…", () => api("/api/indice", {
      chantier: etat.chantier, type_plan: etat.typeIndice, indice: $("#i-indice").value.trim(),
      forcer, ecarter_nouveaux: $("#ecarter-nouveaux").checked,
    }));
    afficherRapport($("#resultat-indice"), r, "indice");
    ajouterBoutonsValidation(r);
    montrerResultat(r);
  } catch { /* déjà signalé */ }
}
$("#caler-indice").addEventListener("click", () => calerIndice(false));
function ajouterBoutonsValidation(r) {
  if (!r.attente) return;
  const nom = r.indice || "ce plan";
  const bloc = document.createElement("div");
  bloc.innerHTML = `<div class="etat-indice">Regardez la vue à droite (rouge = nouvel indice, gris = référence). Si c'est bon :</div>
    <div class="valider"><button class="vert" id="valider-indice">✔ Enregistrer l'indice ${esc(nom)}</button>
    <button id="abandonner-indice">Abandonner</button></div>`;
  const cible = $("#resultat-indice");
  cible.insertBefore(bloc, cible.querySelector(".bandeau").nextSibling);
  $("#valider-indice").addEventListener("click", async () => {
    if (r.rapport.mode === "forcé" && !confirm("Ce plan a été produit SANS contrôle. L'enregistrer quand même ?")) return;
    try {
      const v = await avecAttente("Enregistrement de l'indice…", () => api("/api/indice/valider",
        { chantier: r.chantier, type_plan: r.type_plan, attente: r.attente }));
      bloc.innerHTML = `<div class="bandeau identique">Indice ${esc(nom)} enregistré</div>
        <a class="telecharger" href="${v.telecharger}">Télécharger le DXF calé</a>
        <div class="boutons">${v.precedent ? `<button id="comparer-precedent">Voir ce qui a changé depuis l'indice précédent</button>` : ""}
        <button class="principal" id="retour-chantier">Retour au chantier</button></div>`;
      if (v.precedent) $("#comparer-precedent").addEventListener("click", async () => {
        await ouvrirChantier(etat.chantier, false);
        etat.coches = new Set([v.precedent, v.id]);
        afficherChantier();
        $("#btn-differences").click();
      });
      $("#retour-chantier").addEventListener("click", () => ouvrirChantier(etat.chantier));
      await chargerChantiers();
    } catch { /* déjà signalé */ }
  });
  $("#abandonner-indice").addEventListener("click", async () => {
    try { await api("/api/indice/abandonner", { chantier: r.chantier, type_plan: r.type_plan, attente: r.attente }); }
    catch (e) { toast(e.message); }
    toast("Indice abandonné : rien n'a été enregistré.", true);
    ouvrirIndice(etat.typeIndice);
  });
}

// =========================================================================== divers
$$("[data-ajuster]").forEach((b) => b.addEventListener("click", () => {
  const v = { plan: vPlan, ref: vRef, unique: vUnique }[b.dataset.ajuster];
  if (!v.couches.length) return;
  const emps = v.couches.map((c) => v.empriseCouche(c));
  v.ajuster(v === vUnique && v.couches.length > 1 ? emps[emps.length - 1] : emps[0]);
}));

ouvrirAccueil();
