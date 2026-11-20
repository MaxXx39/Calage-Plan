const { chromium } = require('playwright');
const D = '/home/user/Calage-Plan/tests/data/';
const OUT = process.argv[2] || '.';
const PAIRES = [
  [[16382.052, -16553.176], [2510267.726, 1141751.289]],
  [[19985.355, -14088.02], [2510227.906, 1141769.19]],
  [[21672.818, -15966.025], [2510235.137, 1141793.38]],
];
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 900 }, deviceScaleFactor: 1 });
  const erreurs = [];
  p.on('pageerror', e => erreurs.push(e.message));
  p.on('console', m => { if (m.type() === 'error') erreurs.push(m.text()); });
  await p.goto('http://127.0.0.1:8765/');
  await p.setInputFiles('.depot[data-role="ref"] input', D + 'reference.dwg');
  await p.waitForSelector('.depot[data-role="ref"].charge', { timeout: 60000 });
  await p.setInputFiles('.depot[data-role="plan"] input', D + 'plan_A.dwg');
  await p.waitForSelector('.depot[data-role="plan"].charge', { timeout: 60000 });
  await p.waitForTimeout(300);
  await p.screenshot({ path: OUT + '/e1_fichiers.png' });
  await p.click('#vers-calage');
  // cliquer les points (avec un petit décalage pour tester l'accrochage)
  for (const [pl, rf] of PAIRES) {
    for (const [vue, sel, pt] of [['vPlan', '#c-plan', pl], ['vRef', '#c-ref', rf]]) {
      const ecran = await p.evaluate(([v, pt]) => {
        const vue = v === 'vPlan' ? vPlan : vRef;
        const r = v === 'vPlan' ? 300 : 3;
        vue.ajuster([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r]);
        return vue.absVersEcran(pt[0], pt[1]);
      }, [vue, pt]);
      const box = await p.locator(sel).boundingBox();
      await p.mouse.click(box.x + ecran[0] + 4, box.y + ecran[1] - 3);
      await p.waitForTimeout(400);
    }
  }
  await p.waitForSelector('.verdict');
  await p.evaluate(() => { vPlan.ajuster(etat.geo.plan.emprise); vRef.ajuster(etat.geo.ref.emprise); });
  await p.screenshot({ path: OUT + '/e2_calage.png' });
  console.log('VERDICT:', await p.textContent('.verdict'));
  await p.click('#voir-superposition');
  await p.waitForTimeout(500);
  await p.screenshot({ path: OUT + '/e3_superposition.png' });
  await p.click('#vers-nettoyage');
  await p.waitForSelector('#familles input');
  // écarter quelques calques : ceux qui commencent par ATEC, RS_Cotation, RS_HACHURE-TRES-FONCE
  await p.evaluate(() => {
    for (const c of etat.geo.plan.calques)
      if (/^(ATEC|RS_Cotation|RS_HACHURE-TRES-FONCE|RS_CO-|0$)/.test(c.nom)) etat.gardes.delete(c.nom);
    afficherCalques(); vUnique.dessiner();
  });
  // hachures : cocher
  await p.evaluate(() => { const f = etat.familles.find(f => f.libelle === 'Hachures'); if (f) f.coche = true; preparerNettoyage(); });
  // dessiner une zone (4 clics + double clic) autour du bâtiment
  await p.click('#dessiner-zone');
  const zone = [[2510200, 1141720], [2510290, 1141720], [2510290, 1141820], [2510200, 1141820]];
  await p.evaluate(() => vUnique.ajuster([2510190, 1141710, 2510300, 1141830]));
  const boxU = await p.locator('#c-unique').boundingBox();
  for (let i = 0; i < zone.length; i++) {
    const e = await p.evaluate((pt) => vUnique.absVersEcran(pt[0], pt[1]), zone[i]);
    if (i < zone.length - 1) await p.mouse.click(boxU.x + e[0], boxU.y + e[1]);
    else await p.mouse.dblclick(boxU.x + e[0], boxU.y + e[1]);
    await p.waitForTimeout(150);
  }
  await p.waitForTimeout(300);
  // identifier un objet
  const e0 = await p.evaluate(() => vUnique.absVersEcran(2510243.217, 1141791.34));
  await p.mouse.click(boxU.x + e0[0], boxU.y + e0[1]);
  await p.waitForTimeout(300);
  await p.screenshot({ path: OUT + '/e4_nettoyage.png' });
  console.log('INFO OBJET:', await p.textContent('#info-objet'));
  await p.click('#exporter');
  await p.waitForSelector('#resultat-export .bandeau', { timeout: 120000 });
  await p.waitForTimeout(500);
  await p.screenshot({ path: OUT + '/e5_resultat.png' });
  console.log('RESULTAT:', (await p.textContent('#resultat-export')).slice(0, 600));
  // onglet nouvel indice
  await p.click('.onglet[data-onglet="indice"]');
  await p.waitForTimeout(300);
  await p.setInputFiles('.depot[data-role="indice"] input', process.argv[3] || D + 'plan_A.dwg');
  await p.waitForSelector('.depot[data-role="indice"].charge');
  await p.click('#caler-indice');
  await p.waitForSelector('#resultat-indice .bandeau', { timeout: 120000 });
  await p.waitForTimeout(500);
  await p.screenshot({ path: OUT + '/e6_indice.png' });
  console.log('INDICE:', (await p.textContent('#resultat-indice')).slice(0, 500));
  console.log('ERREURS JS:', erreurs);
  await b.close();
})();
