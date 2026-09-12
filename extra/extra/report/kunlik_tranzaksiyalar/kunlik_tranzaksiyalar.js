// Copyright (c) 2026, abdulloh and contributors
// For license information, please see license.txt
//
// Кунлик транзакциялар — эски дастурдаги жадвал кўриниши: 4 устун
// (Наименование / Кол-во / Цена / ИТОГО). Ҳар бир транзакция: сарлавҳа
// (№ ҳужжат, касса <-> контрагент, сана, вақт, оператор) -> ВЫДАНО /
// ПОЛУЧЕНО қаторлари -> БАЛАНС -> бўш ажратувчи қатор.
//
// Сарлавҳадаги матн — ҳужжат линки: босилса ҳужжат очилади (тузатиш ёки
// бекор қилиш учун).

frappe.query_reports["Kunlik Tranzaksiyalar"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("Сана дан"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": __("Сана гача"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        },
        {
            "fieldname": "transaction_type",
            "label": __("Транзакция тури"),
            "fieldtype": "MultiSelectList",
            "get_data": function() {
                return [
                    { value: "Савдо", description: __("Sales Invoice") },
                    { value: "Тўлов", description: __("Payment Entry") },
                    { value: "Касса/Перемещения", description: __("Journal Entry") },
                    { value: "Товар кўчириш", description: __("Stock Entry") }
                ];
            }
        },
        {
            "fieldname": "pos_profile",
            "label": __("Дўкон (POS Profile)"),
            "fieldtype": "Link",
            "options": "POS Profile"
        },
        {
            "fieldname": "account",
            "label": __("Касса счёт"),
            "fieldtype": "Link",
            "options": "Account",
            "get_query": function() {
                return {
                    filters: { is_group: 0, account_type: ["in", ["Cash", "Bank"]] }
                };
            }
        },
        {
            "fieldname": "party",
            "label": __("Контрагент"),
            "fieldtype": "Link",
            "options": "Customer"
        },
        {
            "fieldname": "only_debt",
            "label": __("Фақат қарзли транзакциялар"),
            "fieldtype": "Check",
            "default": 0
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data || !data.row_type) {
            return value;
        }

        // ── Транзакция сарлавҳаси: қалин; "№ ..." матни ҳужжат линки ──
        if (data.row_type === "header") {
            if (column.fieldname === "title" && data.voucher_no) {
                var url = "/app/" + frappe.router.slug(data.voucher_type) +
                          "/" + encodeURIComponent(data.voucher_no);
                return `<a href="${url}" style="font-weight:700; color:#1a1a1a;">${value}</a>`;
            }
            return `<span style="font-weight:600;">${value}</span>`;
        }

        // ── ВЫДАНО / ПОЛУЧЕНО бўлимлари ──
        if (data.row_type === "section" && column.fieldname === "title") {
            var color = data.title === "ПОЛУЧЕНО" ? "#1f7a3d" : "#8a5a00";
            return `<span style="font-weight:700; color:${color};">${value}</span>`;
        }

        // ── БАЛАНС: манфий бўлса қизил (қарз), акс ҳолда кўк ──
        if (data.row_type === "balance" && value) {
            var negative = String(data.amount || "").trim().startsWith("-");
            var balance_color = negative ? "#c0392b" : "#2d6cdf";
            return `<span style="font-weight:700; color:${balance_color};">${value}</span>`;
        }

        return value;
    }
};
