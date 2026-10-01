// Sales Invoice: "Umumiy chegirma" (summa yoki %) ni POS'dagi kabi tovar
// qatorlariga taqsimlash.
//
// Kiritilgan summa qatorlarga ularning chegirmasiz summasiga proporsional
// bo'linadi va har qatorning discount_amount (BIR DONAGA) maydoniga qo'shiladi:
// rate = price_list_rate - discount_amount — jadvalda har tovarning chegirmasi
// ko'rinadi. Har qatorga qo'shilgan ulush extra_discount_unit'da saqlanadi:
// summa o'zgarsa (yoki qty o'zgarsa / qator o'chirilsa) avvalgi ulush qaytarilib,
// yangi summa qayta taqsimlanadi. "Umumiy chegirma" maydoni kiritilgan qiymatni
// saqlab turadi.
//
// Standart discount_amount (yashirin) faqat dona narxga bo'linmaydigan bir
// necha tiyin qoldiqni oladi — jami summa kiritilgan qiymatga aniq teng.
// Chegirma tovarlar summasidan oshsa POS kabi qirqiladi (eng ko'pi 100%).
// Qaytarish, "cash/non trade discount" va soliqli Grand Total chegirmasida
// taqsimlanmaydi: butun summa standart discount_amount'ga yoziladi.

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		// Eski qoralamalardagi standart qo'shimcha chegirmani yangi maydonda ko'rsatish
		const doc = frm.doc;
		if (
			doc.docstatus === 0 &&
			flt(doc.discount_amount) &&
			!flt(doc.extra_discount_amount) &&
			!(doc.items || []).some((d) => flt(d.extra_discount_unit))
		) {
			doc.extra_discount_amount = flt(doc.discount_amount);
			frm.refresh_field("extra_discount_amount");
		}
	},

	extra_discount_amount(frm) {
		if (frm.__extra_distributing) return;
		frm.doc.extra_discount_percentage = 0;
		extra_apply_total_discount(frm);
	},

	extra_discount_percentage(frm) {
		if (frm.__extra_distributing) return;
		extra_apply_total_discount(frm);
	},
});

frappe.ui.form.on("Sales Invoice Item", {
	qty(frm) {
		extra_schedule_redistribution(frm);
	},
	items_remove(frm) {
		extra_schedule_redistribution(frm);
	},
});

function extra_schedule_redistribution(frm) {
	if (!flt(frm.doc.extra_discount_amount) && !flt(frm.doc.extra_discount_percentage)) return;
	// ERPNext'ning narx/pricing rule so'rovlari tugagach qayta taqsimlaymiz
	frappe.after_ajax(() => setTimeout(() => extra_apply_total_discount(frm), 0));
}

function extra_set_item_rate(d, new_rate, prec) {
	const base = flt(d.rate_with_margin) || flt(d.price_list_rate);
	d.discount_amount = flt(base - new_rate, prec);
	d.discount_percentage = base ? flt((100 * d.discount_amount) / base, 6) : 0;
	d.rate = flt(new_rate, prec);
}

function extra_apply_total_discount(frm) {
	const doc = frm.doc;
	if (doc.docstatus !== 0) return;
	const items = doc.items || [];
	const prec = items.length ? precision("rate", items[0]) : 2;

	// 1. Avvalgi taqsimotni qaytarish
	items.forEach((d) => {
		const unit = flt(d.extra_discount_unit);
		if (!unit) return;
		extra_set_item_rate(d, flt(d.rate) + unit, prec);
		d.extra_discount_unit = 0;
	});

	const lines = items.filter((d) => !cint(d.is_free_item) && flt(d.qty) > 0 && flt(d.rate) > 0);
	const gross = lines.reduce((s, d) => s + flt(d.rate) * flt(d.qty), 0);

	let target = flt(doc.extra_discount_amount);
	if (flt(doc.extra_discount_percentage)) {
		target = flt((gross * flt(doc.extra_discount_percentage)) / 100, precision("extra_discount_amount"));
		doc.extra_discount_amount = target;
	}

	// 2. Yangi summani taqsimlash
	let distributed = 0;
	const can_distribute =
		target > 0 &&
		gross > 0 &&
		!cint(doc.is_return) &&
		!cint(doc.is_cash_or_non_trade_discount) &&
		!(doc.apply_discount_on === "Grand Total" && flt(doc.total_taxes_and_charges));

	if (can_distribute) {
		// POS kabi: chegirma tovarlar summasidan oshmaydi — ortiqchasi qirqiladi
		// (eng ko'pi 100%, jami 0)
		if (target > flt(gross, prec)) {
			target = flt(gross, prec);
			doc.extra_discount_amount = target;
			if (flt(doc.extra_discount_percentage)) doc.extra_discount_percentage = 100;
			frappe.show_alert({
				message: __("Chegirma tovarlar summasidan oshmaydi. Qo'llangan chegirma: {0}", [
					format_currency(target, doc.currency),
				]),
				indicator: "orange",
			});
		}
		const units = extra_split_discount(lines, target, prec);
		lines.forEach((d) => {
			const unit = units[d.name];
			if (!unit) return;
			// Narxi Price List'dan kelmagan (qo'lda kiritilgan) tovar uchun baza = joriy narx
			if (!flt(d.price_list_rate)) d.price_list_rate = flt(d.rate);
			extra_set_item_rate(d, flt(d.rate) - unit, prec);
			d.extra_discount_unit = unit;
			distributed += unit * flt(d.qty);
		});
		distributed = flt(distributed, prec);
	}

	frm.__extra_distributing = true;
	doc.additional_discount_percentage = 0;
	doc.discount_amount = flt(target - distributed, precision("discount_amount"));
	frm.cscript.calculate_taxes_and_totals();
	frm.refresh_fields();
	frm.dirty();
	frm.__extra_distributing = false;
}

// POS'dagi distribute_new_total bilan bir xil: proporsional, dona chegirmasi
// pastga yaxlitlanadi, qoldiq "eng katta kamomad"li qatorga step qadamlar bilan.
function extra_split_discount(lines, amount, prec) {
	const step = 1 / Math.pow(10, prec);
	const gross = lines.reduce((s, d) => s + flt(d.rate) * flt(d.qty), 0);
	const raw = {};
	const units = {};
	lines.forEach((d) => {
		raw[d.name] = (amount * flt(d.rate) * flt(d.qty)) / gross;
		units[d.name] = Math.min(
			Math.floor((raw[d.name] / flt(d.qty)) * Math.pow(10, prec) + 1e-6) / Math.pow(10, prec),
			flt(d.rate)
		);
	});

	let rem = flt(amount - lines.reduce((s, d) => s + units[d.name] * flt(d.qty), 0), prec);
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
	return units;
}
