// Sales Invoice: qo'shimcha chegirmani (Additional Discount Amount / %) POS'dagi
// kabi tovar qatorlariga taqsimlash.
//
// Kiritilgan summa qatorlarga ularning joriy summasi (amount) ga proporsional
// bo'linadi va har qatorning discount_amount (BIR DONAGA) maydoniga qo'shiladi:
// rate = price_list_rate - discount_amount. Shu tufayli jadvalda har tovar
// bo'yicha qancha chegirma berilgani ko'rinadi. Dona narxni tiyingacha bo'lib
// bo'lmaydigan qoldiq (qty > 1 bo'lganda bir necha tiyin) hujjat darajasidagi
// discount_amount'da qoladi — jami summa kiritilgan qiymatga aniq teng bo'ladi.
//
// Soliq bor va chegirma Grand Total'ga qo'llanadigan hujjatlarda, qaytarishlarda
// va "cash/non trade discount" rejimida ERPNext'ning standart xulqi saqlanadi.

frappe.ui.form.on("Sales Invoice", {
	discount_amount(frm) {
		if (frm.__extra_distributing) return;
		// ERPNext'ning o'z discount_amount/percentage handlerlari tugashini kutamiz
		setTimeout(() => extra_distribute_additional_discount(frm), 0);
	},
});

function extra_distribute_additional_discount(frm) {
	const doc = frm.doc;
	const discount = flt(doc.discount_amount);
	if (doc.docstatus !== 0 || discount <= 0) return;
	if (cint(doc.is_return) || cint(doc.is_cash_or_non_trade_discount)) return;
	if (doc.apply_discount_on === "Grand Total" && flt(doc.total_taxes_and_charges)) return;

	const lines = (doc.items || []).filter(
		(d) => !cint(d.is_free_item) && flt(d.qty) > 0 && flt(d.rate) > 0
	);
	if (!lines.length) return;

	const prec = precision("rate", lines[0]);
	const step = 1 / Math.pow(10, prec);
	const gross = lines.reduce((s, d) => s + flt(d.rate) * flt(d.qty), 0);
	if (gross <= 0 || discount >= gross) return;
	const amount = discount;

	// 1-bosqich: proporsional, dona chegirmasi pastga yaxlitlanadi
	const raw = {};
	const units = {};
	lines.forEach((d) => {
		raw[d.name] = (amount * flt(d.rate) * flt(d.qty)) / gross;
		units[d.name] = Math.min(
			Math.floor(((raw[d.name] / flt(d.qty)) * Math.pow(10, prec)) + 1e-6) / Math.pow(10, prec),
			flt(d.rate)
		);
	});
	const given = () => lines.reduce((s, d) => s + units[d.name] * flt(d.qty), 0);

	// 2-bosqich: qoldiq "eng katta kamomad"li qatorga step qadamlar bilan
	let rem = flt(amount - given(), prec);
	for (let guard = 10000; rem > 1e-9 && guard > 0; guard--) {
		const candidates = lines.filter(
			(d) => units[d.name] + step <= flt(d.rate) + 1e-9 && step * flt(d.qty) <= rem + 1e-9
		);
		if (!candidates.length) break;
		const best = candidates.reduce((a, b) =>
			raw[b.name] - units[b.name] * flt(b.qty) > raw[a.name] - units[a.name] * flt(a.qty) ? b : a
		);
		units[best.name] = flt(units[best.name] + step, prec);
		rem = flt(rem - step * flt(best.qty), prec);
	}

	const distributed = flt(amount - Math.max(rem, 0), prec);
	if (distributed <= 0) return;

	lines.forEach((d) => {
		const unit = units[d.name];
		if (!unit) return;
		const new_rate = flt(flt(d.rate) - unit, prec);
		// Narxi Price List'dan kelmagan (qo'lda kiritilgan) tovar uchun baza = joriy narx
		if (!flt(d.price_list_rate)) d.price_list_rate = flt(d.rate);
		const base = flt(d.rate_with_margin) || flt(d.price_list_rate);
		d.discount_amount = flt(base - new_rate, prec);
		d.discount_percentage = base ? flt((100 * d.discount_amount) / base, 6) : 0;
		d.rate = new_rate;
	});

	frm.__extra_distributing = true;
	doc.additional_discount_percentage = 0;
	doc.discount_amount = flt(discount - distributed, precision("discount_amount"));
	frm.cscript.calculate_taxes_and_totals();
	frm.refresh_fields();
	frm.dirty();
	frm.__extra_distributing = false;

	frappe.show_alert({
		message: __("Chegirma {0} tovarlarga taqsimlandi", [
			format_currency(distributed, doc.currency),
		]),
		indicator: "green",
	});
}
