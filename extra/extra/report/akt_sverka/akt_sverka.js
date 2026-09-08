frappe.query_reports["Akt Sverka"] = {
    "onload": function(report) {

        // ── Existing back button ──────────────────────────────────────
        report.page.add_inner_button(__("Контрагент Отчётга Қайтиш"), function() {
            var from_date  = frappe.query_report.get_filter_value('from_date');
            var to_date    = frappe.query_report.get_filter_value('to_date');
            var party_type = frappe.query_report.get_filter_value('party_type');

            frappe.set_route('query-report', 'Kontragent Otchet', {
                from_date:  from_date,
                to_date:    to_date,
                party_type: party_type || ''
            });
        });

        // ── PDF download button ───────────────────────────────────────
        report.page.add_inner_button(__("PDF юклаш"), function() {

            var filters = frappe.query_report.get_values();

            // Validate required fields before hitting server
            if (!filters || !filters.party) {
                frappe.msgprint({
                    title:   __("Хато"),
                    message: __("Контрагентни танланг"),
                    indicator: "red"
                });
                return;
            }
            if (!filters.from_date || !filters.to_date) {
                frappe.msgprint({
                    title:   __("Хато"),
                    message: __("Санани кўрсатинг"),
                    indicator: "red"
                });
                return;
            }

            // Freeze UI while generating
            frappe.dom.freeze(__("PDF тайёрланмоқда... Илтимос кутинг"));

            frappe.call({
                method: "extra.extra.report.akt_sverka.akt_sverka.generate_akt_sverka_pdf",
                args:   { filters: filters },
                timeout: 120,   // large reports can take time

                callback: function(r) {
                    frappe.dom.unfreeze();

                    if (!r.message) {
                        frappe.msgprint({
                            title:   __("Хато"),
                            message: __("PDF бўш қайтди. Error Log ни текширинг."),
                            indicator: "red"
                        });
                        return;
                    }

                    // Decode base64 → Blob → trigger download
                    try {
                        var byteChars   = atob(r.message);
                        var byteNumbers = new Array(byteChars.length);
                        for (var i = 0; i < byteChars.length; i++) {
                            byteNumbers[i] = byteChars.charCodeAt(i);
                        }
                        var byteArray = new Uint8Array(byteNumbers);
                        var blob      = new Blob([byteArray], { type: "application/pdf" });
                        var url       = URL.createObjectURL(blob);

                        var filename = "Akt_Sverka_"
                            + (filters.party      || "")
                            + "_" + (filters.from_date || "")
                            + "_" + (filters.to_date   || "")
                            + ".pdf";

                        var a = document.createElement("a");
                        a.href     = url;
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);

                    } catch(e) {
                        frappe.msgprint({
                            title:   __("Хато"),
                            message: __("PDF юклашда браузер хатоси: ") + e.message,
                            indicator: "red"
                        });
                    }
                },

                error: function(r) {
                    frappe.dom.unfreeze();
                    frappe.msgprint({
                        title:   __("Сервер хатоси"),
                        message: __("PDF генерация қилишда хато. Error Log ни текширинг."),
                        indicator: "red"
                    });
                }
            });
        });
    },

    // ── Filters ──────────────────────────────────────────────────────
    "filters": [
        {
            "fieldname": "from_date",
            "label":     __("Сана дан"),
            "fieldtype": "Date",
            "default":   frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            "reqd":      1
        },
        {
            "fieldname": "to_date",
            "label":     __("Сана гача"),
            "fieldtype": "Date",
            "default":   frappe.datetime.get_today(),
            "reqd":      1
        },
        {
            "fieldname": "party_type",
            "label":     __("Контрагент тури"),
            "fieldtype": "Select",
            "options":   "\nCustomer\nSupplier\nEmployee\nOther",
            "default":   "Customer",
            "reqd":      1
        },
        {
            "fieldname": "party",
            "label":     __("Контрагент"),
            "fieldtype": "Dynamic Link",
            "reqd":      1,
            "get_options": function() {
                var party_type = frappe.query_report.get_filter_value('party_type');
                var party      = frappe.query_report.get_filter_value('party');
                if (party && !party_type) {
                    frappe.throw(__("Please select Party Type first"));
                }
                return party_type;
            }
        }
    ],

    // ── Formatter ────────────────────────────────────────────────────
    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        // Strip $ from currency fields
        if (column.fieldtype === "Currency" && value) {
            value = value.replace(/\$/g, '');
        }

        // Bold + highlight Total row
        if (data && data.voucher_type === "Total") {
            value = `<span style="font-weight:bold; background-color:#e3f2fd;">${value}</span>`;
        }

        return value;
    }
};
