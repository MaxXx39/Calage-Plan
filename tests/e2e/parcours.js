// Parcours complet de l'appli dans un vrai navigateur (Playwright).
// Pré-requis : serveur lancé sur un dossier de travail VIDE :
//   python -m calage interface --sans-navigateur --dossier <dossier vide>
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
  const log = (...a) => console.log(...a);
  p.on('pageerror', (e) => erreurs.push(e.message));
  p.on('dialog', (d) => { log('DIALOGUE :', d.message().split('\n')[0]); d.accept(); });
  const texte = async (sel) => (await p.textContent(sel)).replace(/\s+/g, ' ').trim();

  // ---------- 1. nouveau chantier
  await p.goto('http://127.0.0.1:8765/');
  await p.click('#btn-nouveau-chantier');
  await p.fill('#nc-nom', 'Gland City One');
  await p.setInputFiles('#nc-depot input', D + 'reference.dwg');
  await p.waitForSelector('#nc-depot.charge', { timeout: 60000 });
  await p.click('#nc-creer');
  await p.waitForSelector('#e-chantier:not([hidden])');
  await p.screenshot({ path: OUT + '/v3_chantier_vide.png' });
  log('ARIANE', await texte('#ariane'));

  // ---------- 2. ajouter un plan : calage sur 3 axes + 1 vérification
  await p.click('#btn-ajouter-plan');
  await p.waitForFunction(() => vRef.couches.length > 0, null, { timeout: 60000 });
  await p.fill('#type-plan', 'Terrassement');
  await p.setInputFiles('#e-ajout .depot[data-role="plan"] input', D + 'plan_A.dwg');
  await p.waitForSelector('#e-ajout .depot[data-role="plan"].charge', { timeout: 60000 });
  await p.click('#vers-calage');
  await p.selectOption('[data-type="0"]', 'axe');
  await p.selectOption('[data-type="1"]', 'axe');
  await p.click('#ajout-axe');                       // 3e axe, inséré avant la vérification
  const saisies = [
    ['vPlan', '#c-plan', [17790.70, -16551.99]], ['vRef', '#c-ref', [2510260.373, 1141763.304]],    // façade longue 1
    ['vPlan', '#c-plan', [17945.258, -14089.889]], ['vRef', '#c-ref', [2510238.556, 1141751.7895]], // façade longue 2
    ['vPlan', '#c-plan', [16334.36, -16307.03]], ['vRef', '#c-ref', [2510258.466, 1141742.839]],    // petit côté
    ['vPlan', '#c-plan', [21672.818, -15966.025]], ['vRef', '#c-ref', [2510235.137, 1141793.38]],   // vérification
  ];
  let k = 0;
  for (const [vue, sel, pt] of saisies) {
    const ecran = await p.evaluate(([v, pt]) => {
      const vue = v === 'vPlan' ? vPlan : vRef; const r = v === 'vPlan' ? 120 : 1.2;
      vue.ajuster([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r]);
      return vue.absVersEcran(pt[0], pt[1]);
    }, [vue, pt]);
    const box = await p.locator(sel).boundingBox();
    await p.mouse.move(box.x + ecran[0] + 2, box.y + ecran[1] - 1);
    await p.waitForTimeout(250);
    if (k === 1) await p.screenshot({ path: OUT + '/v3_survol_axe.png' });
    await p.mouse.click(box.x + ecran[0] + 2, box.y + ecran[1] - 1);
    await p.waitForTimeout(350);
    k++;
  }
  await p.waitForSelector('.verdict');
  await p.evaluate(() => { vPlan.ajuster(etat.geo.plan.emprise); vRef.ajuster(etat.geo.ref.emprise); });
  await p.waitForTimeout(200);
  await p.screenshot({ path: OUT + '/v3_calage_3axes.png' });
  log('CALAGE 3 AXES', (await texte('#resultat-calage')).slice(0, 170));

  // ---------- 3. nettoyage : calques au clic, hachures en contour, zone découpée
  await p.click('#vers-nettoyage');
  await p.waitForSelector('#familles input');
  const boxU = await p.locator('#c-unique').boundingBox();
  await p.evaluate(() => vUnique.ajuster([2510225, 1141730, 2510275, 1141800]));
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
    await p.waitForTimeout(100);
    await p.mouse.click(boxU.x + cible.pos[0], boxU.y + cible.pos[1]);
    await p.waitForTimeout(120);
  }
  await p.click('#mode-ecarter');
  log('CALQUES', await texte('#compte-calques'));
  await p.check('input[name="hachures"][value="contour"]');
  await p.evaluate(() => vUnique.ajuster([2510190, 1141710, 2510300, 1141830]));
  await p.click('#dessiner-zone');
  const zone = [[2510200, 1141720], [2510290, 1141720], [2510290, 1141775], [2510200, 1141775]];
  for (let i = 0; i < zone.length; i++) {
    const e = await p.evaluate((pt) => vUnique.absVersEcran(pt[0], pt[1]), zone[i]);
    if (i < zone.length - 1) await p.mouse.click(boxU.x + e[0], boxU.y + e[1]);
    else await p.mouse.dblclick(boxU.x + e[0], boxU.y + e[1]);
    await p.waitForTimeout(120);
  }
  await p.screenshot({ path: OUT + '/v3_nettoyage.png' });
  await p.click('#exporter');
  await p.waitForSelector('#fin-retour', { timeout: 120000 });
  await p.waitForTimeout(400);
  await p.screenshot({ path: OUT + '/v3_plan_enregistre.png' });
  log('ENREGISTRE', (await texte('#resultat-export .alertes')).slice(0, 200));
  await p.click('#fin-retour');
  await p.waitForSelector('#e-chantier:not([hidden]) .carte-plan');
  await p.waitForTimeout(400);
  await p.screenshot({ path: OUT + '/v3_chantier_un_plan.png' });

  // ---------- 4. nouvel indice reçu
  await p.click('.nouvel-indice');
  await p.waitForSelector('#e-indice:not([hidden])');
  log('INDICE PROPOSE', await p.inputValue('#i-indice'));
  await p.setInputFiles('#e-indice .depot[data-role="indice"] input', PLAN_B);
  await p.waitForSelector('#e-indice .depot[data-role="indice"].charge');
  await p.click('#caler-indice');
  await p.waitForSelector('#valider-indice', { timeout: 120000 });
  await p.screenshot({ path: OUT + '/v3_indice_a_valider.png' });
  log('INDICE', (await texte('#resultat-indice .bandeau')).slice(0, 110));
  await p.click('#valider-indice');
  await p.waitForSelector('#comparer-precedent');
  await p.click('#comparer-precedent');
  await p.waitForSelector('#retour-superposition', { timeout: 120000 });
  await p.waitForTimeout(400);
  await p.screenshot({ path: OUT + '/v3_differences.png' });
  log('DIFF', (await texte('#legende')).slice(0, 150));

  // ---------- 5. un autre plan du chantier, calage automatique
  await p.click('#btn-ajouter-plan');
  await p.waitForFunction(() => vRef.couches.length > 0, null, { timeout: 60000 });
  await p.fill('#type-plan', 'Canalisations');
  await p.setInputFiles('#e-ajout .depot[data-role="plan"] input', PLAN_B);
  await p.waitForSelector('#e-ajout .depot[data-role="plan"].charge', { timeout: 60000 });
  await p.click('#vers-calage');
  await p.click('#calage-auto');
  await p.waitForSelector('#bandeau-auto .verdict', { timeout: 120000 });
  await p.waitForTimeout(400);
  await p.screenshot({ path: OUT + '/v3_calage_auto.png' });
  log('AUTO', (await texte('#bandeau-auto')).slice(0, 120));

  // ---------- 6. supprimer un indice (retour au chantier par le fil d'Ariane)
  await p.click('#ariane a[data-nav="chantier"]');
  await p.waitForSelector('#e-chantier:not([hidden]) .carte-plan');
  await p.click('.carte-plan .historique summary');
  const avant = await p.locator('.suppr-indice').count();
  await p.locator('.suppr-indice').first().click();
  await p.waitForTimeout(1200);
  log('SUPPRESSION indice :', avant, '->', await p.locator('.suppr-indice').count());
  log('ERREURS JS', erreurs);
  await b.close();
})();
