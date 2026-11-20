// Parcours complet de l'appli dans un vrai navigateur (Playwright).
// Pré-requis : serveur lancé (python -m calage interface --sans-navigateur --dossier <dossier vide>)
// Usage : node tests/e2e/parcours.js <dossier_captures> <plan_indice_B_decale.dxf>
const { chromium } = require('playwright');
const path = require('path');
const D = path.join(__dirname, '..', 'data') + path.sep;
const OUT = process.argv[2] || '.';
const PLAN_B = process.argv[3];
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
  const erreurs = [];
  p.on('pageerror', (e) => erreurs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error') erreurs.push(m.text()); });
  const log = (...a) => console.log(...a);
  p.on('dialog', (d) => { log('DIALOGUE', d.message()); d.accept(); });
  await p.goto('http://127.0.0.1:8765/');
  // ---------- 1. chantier + fichiers
  await p.fill('#chantier', 'Gland City One');
  await p.fill('#type-plan', 'Terrassement');
  await p.fill('#indice', 'A');
  await p.setInputFiles('.depot[data-role="ref"] input', D + 'reference.dwg');
  await p.waitForSelector('.depot[data-role="ref"].charge', { timeout: 60000 });
  await p.setInputFiles('.depot[data-role="plan"] input', D + 'plan_A.dwg');
  await p.waitForSelector('.depot[data-role="plan"].charge', { timeout: 60000 });
  await p.click('#vers-calage');
  // ---------- 2. calage : 2 axes + 1 point de calage + 1 point de contrôle
  await p.selectOption('[data-type="0"]', 'axe');
  await p.selectOption('[data-type="1"]', 'axe');
  await p.click('#ajout-point');
  const saisies = [
    ['vPlan', '#c-plan', [17790.70, -16551.99]], ['vRef', '#c-ref', [2510260.373, 1141763.304]],   // axe 1
    ['vPlan', '#c-plan', [16143.6, -15322.47]], ['vRef', '#c-ref', [2510258.466, 1141742.839]],    // axe 2
    ['vPlan', '#c-plan', [19985.355, -14088.02]], ['vRef', '#c-ref', [2510227.906, 1141769.19]],   // contrôle
    ['vPlan', '#c-plan', [21672.818, -15966.025]], ['vRef', '#c-ref', [2510235.137, 1141793.38]],  // point
  ];
  let k = 0;
  for (const [vue, sel, pt] of saisies) {
    const ecran = await p.evaluate(([v, pt]) => {
      const vue = v === 'vPlan' ? vPlan : vRef; const r = v === 'vPlan' ? 120 : 1.2;
      vue.ajuster([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r]);
      return vue.absVersEcran(pt[0], pt[1]);
    }, [vue, pt]);
    const box = await p.locator(sel).boundingBox();
    await p.mouse.move(box.x + ecran[0] + 2, box.y + ecran[1] - 1);   // survol : accroche visible avant le clic
    await p.waitForTimeout(250);
    const survol = await p.evaluate((v) => JSON.stringify((v === 'vPlan' ? vPlan : vRef).survol), vue);
    if (k === 1) await p.screenshot({ path: OUT + '/v2_survol_axe.png' });
    if (k === 5) await p.screenshot({ path: OUT + '/v2_survol_point.png' });
    log('survol', k, survol ? survol.slice(0, 90) : survol);
    await p.mouse.click(box.x + ecran[0] + 2, box.y + ecran[1] - 1);
    await p.waitForTimeout(350);
    k++;
  }
  await p.waitForSelector('.verdict');
  await p.evaluate(() => { vPlan.ajuster(etat.geo.plan.emprise); vRef.ajuster(etat.geo.ref.emprise); });
  await p.waitForTimeout(200);
  await p.screenshot({ path: OUT + '/v2_calage_axes.png' });
  log('VERDICT', (await p.textContent('#resultat-calage')).replace(/\s+/g, ' ').slice(0, 200));
  // ---------- 3. nettoyage : un clic sur un objet = tout son calque
  await p.click('#vers-nettoyage');
  await p.waitForSelector('#familles input');
  const boxU = await p.locator('#c-unique').boundingBox();
  await p.evaluate(() => vUnique.ajuster([2510225, 1141730, 2510275, 1141800]));
  await p.waitForTimeout(200);
  await p.click('#mode-ecarter');
  for (let essai = 0; essai < 12; essai++) {
    const cible = await p.evaluate(() => {
      const geo = etat.geo.plan;
      for (const g of geo.groupes) {
        const nom = geo.calques[g.l].nom;
        if (!/^(ATEC|RS_CO-|RS_HACHURE-TRES-FONCE)/.test(nom) || !etat.gardes.has(nom) || !couleurPlanSuperpose(g)) continue;
        const l = g.lignes[0];
        const [ax, ay] = appliquerSim(etat.sim, (l[0] + l[2]) / 2 + geo.origine[0], (l[1] + l[3]) / 2 + geo.origine[1]);
        return { pos: vUnique.absVersEcran(ax, ay), nom };
      }
      return null;
    });
    if (!cible) break;
    await p.mouse.move(boxU.x + cible.pos[0], boxU.y + cible.pos[1]);
    await p.waitForTimeout(120);
    const bulle = await p.textContent('#bulle');
    await p.mouse.click(boxU.x + cible.pos[0], boxU.y + cible.pos[1]);
    await p.waitForTimeout(150);
    log('clic écarter :', cible.nom, '| bulle :', bulle);
  }
  await p.click('#mode-ecarter');
  const pos = await p.evaluate(() => vUnique.absVersEcran(2510243.217, 1141791.34));
  await p.mouse.click(boxU.x + pos[0], boxU.y + pos[1]);
  await p.waitForTimeout(200);
  log('SELECTION', (await p.textContent('#info-objet')).replace(/\s+/g, ' '));
  await p.screenshot({ path: OUT + '/v2_nettoyage.png' });
  await p.click('#exporter');
  await p.waitForSelector('#resultat-export .bandeau', { timeout: 120000 });
  log('EXPORT', (await p.textContent('#resultat-export .bandeau')).trim());
  // ---------- nouvel indice (B décalé)
  await p.click('.onglet[data-onglet="indice"]');
  await p.waitForTimeout(400);
  await p.fill('#i-indice', 'B');
  await p.setInputFiles('.depot[data-role="indice"] input', PLAN_B);
  await p.waitForSelector('.depot[data-role="indice"].charge');
  await p.click('#caler-indice');
  await p.waitForSelector('#resultat-indice .bandeau', { timeout: 120000 });
  log('INDICE', (await p.textContent('#resultat-indice .bandeau')).trim().slice(0, 80));
  // ---------- chantiers : différences A -> B
  await p.click('.onglet[data-onglet="chantiers"]');
  await p.waitForSelector('#arbre input');
  await p.waitForTimeout(800);                 // la liste se rafraîchit à l'ouverture de l'onglet
  const n = await p.locator('#arbre input').count();
  for (let i = 0; i < n; i++) await p.locator('#arbre input').nth(i).check();
  await p.click('#differences');
  await p.waitForSelector('#legende div', { timeout: 120000 });
  await p.waitForTimeout(400);
  await p.screenshot({ path: OUT + '/v2_differences.png' });
  log('DIFF', (await p.textContent('#legende')).replace(/\s+/g, ' ').slice(0, 160));
  // ---------- calage automatique d'un autre type de plan du même chantier
  await p.reload();
  await p.fill('#chantier', 'Gland City One');
  await p.fill('#type-plan', 'Canalisations');
  await p.waitForTimeout(600);
  await p.click('#ref-chantier');
  await p.waitForSelector('.depot[data-role="ref"].charge', { timeout: 60000 });
  await p.setInputFiles('.depot[data-role="plan"] input', PLAN_B);
  await p.waitForSelector('.depot[data-role="plan"].charge', { timeout: 60000 });
  await p.click('#vers-calage');
  await p.click('#calage-auto');
  await p.waitForSelector('#bandeau-auto .verdict', { timeout: 120000 });
  await p.waitForTimeout(500);
  await p.screenshot({ path: OUT + '/v2_calage_auto.png' });
  log('AUTO', (await p.textContent('#bandeau-auto')).replace(/\s+/g, ' ').slice(0, 160));
  log('ERREURS JS', erreurs);
  await b.close();
})();
