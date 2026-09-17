// Copyright (c) 2025, abdulloh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Kassa", {
    refresh: function(frm) {
        // Linked document button
        if (frm.doc.docstatus == 1 && frm.doc.linked_entry) {
            frm.add_custom_button(frm.doc.linked_entry, function() {
                frappe.set_route("Form", frm.doc.linked_doctype, frm.doc.linked_entry);
            }, __("Связанный документ"));
        }

        // Set expense account query
        frm.set_query("expense_account", function() {
            return {
                filters: {
                    company: frm.doc.company,
                    root_type: "Expense",
                    is_group: 0
                }
            };
        });

        // Set mode_of_payment query
        frm.trigger("set_mode_of_payment_query");

        // Update balance on refresh
        if (frm.doc.mode_of_payment && frm.doc.company) {
            frm.trigger("update_balance");
        }

        // Update balance_to on refresh for transfer
        if (frm.doc.mode_of_payment_to && frm.doc.company) {
            frm.trigger("update_balance_to");
        }

        // Set mode_of_payment_to query
        frm.trigger("set_mode_of_payment_to_query");

        // Update balance label based on transaction type
        frm.trigger("update_balance_label");
        frm.trigger("update_party_type_options");
        frm.trigger("set_exchange_rate_description");

        if (frm.doc.transaction_type === "Конвертация" && !frm.doc.exchange_rate) {
            frm.trigger("fetch_exchange_rate");
        }
    },

    company: function(frm) {
        frm.set_value("mode_of_payment", "");
        frm.set_value("cash_account", "");
        frm.set_value("cash_account_currency", "");
        frm.set_value("balance", 0);
        frm.set_value("mode_of_payment_to", "");
        frm.set_value("cash_account_to", "");
        frm.set_value("cash_account_to_currency", "");
        frm.set_value("balance_to", 0);
        frm.set_value("exchange_rate", 0);
        frm.set_value("debit_amount", 0);
        frm.set_value("credit_amount", 0);
        frm.set_value("manual_credit_amount", 0);
        frm.set_value("party", "");
        frm.set_value("expense_account", "");
    },

    mode_of_payment: function(frm) {
        if (frm.doc.mode_of_payment && frm.doc.company) {
            frappe.call({
                method: "extra.extra.doctype.kassa.kassa.get_cash_account_with_currency",
                args: {
                    mode_of_payment: frm.doc.mode_of_payment,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message && r.message.account) {
                        frm.set_value("cash_account", r.message.account);
                        frm.set_value("cash_account_currency", r.message.currency);
                        frm.trigger("update_balance");
                        frm.trigger("validate_currency");
                        if (frm.doc.transaction_type === "Конвертация") {
                            frm.trigger("fetch_exchange_rate");
                            frm.trigger("calculate_conversion_amount");
                        }
                    } else {
                        frappe.msgprint(__("Для данного способа оплаты не настроен счет кассы для компании {0}", [frm.doc.company]));
                        frm.set_value("cash_account", "");
                        frm.set_value("cash_account_currency", "");
                        frm.set_value("balance", 0);
                    }
                }
            });
        } else {
            frm.set_value("cash_account", "");
            frm.set_value("cash_account_currency", "");
            frm.set_value("balance", 0);
        }

        // Source o'zgarsa transfer/conversion manzilini qayta tanlash kerak.
        if (in_list(["Перемещения", "Конвертация"], frm.doc.transaction_type)) {
            frm.set_value("mode_of_payment_to", "");
            frm.set_value("cash_account_to", "");
            frm.set_value("cash_account_to_currency", "");
            frm.set_value("balance_to", 0);
            frm.set_value("manual_credit_amount", 0);
            frm.trigger("set_mode_of_payment_to_query");
        }
    },

    update_balance: function(frm) {
        if (frm.doc.cash_account && frm.doc.company) {
            frappe.call({
                method: "extra.extra.doctype.kassa.kassa.get_account_balance",
                args: {
                    account: frm.doc.cash_account,
                    company: frm.doc.company
                },
                callback: function(r) {
                    frm.set_value("balance", r.message || 0);
                }
            });
        }
    },

    transaction_type: function(frm) {
        // Clear party fields
        frm.set_value("party_type", "");
        frm.set_value("party", "");
        frm.set_value("expense_account", "");
        frm.set_value("party_name", "");
        frm.set_value("expense_account_name", "");

        // Clear payment and transfer fields
        frm.set_value("mode_of_payment", "");
        frm.set_value("cash_account", "");
        frm.set_value("balance", 0);
        frm.set_value("mode_of_payment_to", "");
        frm.set_value("cash_account_to", "");
        frm.set_value("cash_account_to_currency", "");
        frm.set_value("balance_to", 0);
        frm.set_value("exchange_rate", 0);
        frm.set_value("debit_amount", 0);
        frm.set_value("credit_amount", 0);
        frm.set_value("manual_credit_amount", 0);

        // Set queries
        frm.trigger("set_mode_of_payment_query");
        frm.trigger("set_mode_of_payment_to_query");
        frm.trigger("update_party_type_options");

        // For Перемещения, set default company if not set
        if (frm.doc.transaction_type === "Перемещения" && !frm.doc.company) {
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Global Defaults",
                    fieldname: "default_company"
                },
                callback: function(r) {
                    if (r.message && r.message.default_company) {
                        frm.set_value("company", r.message.default_company);
                    }
                }
            });
        }

        // Update balance label
        frm.trigger("update_balance_label");
    },

    mode_of_payment_to: function(frm) {
        if (frm.doc.mode_of_payment_to && frm.doc.company) {
            frappe.call({
                method: "extra.extra.doctype.kassa.kassa.get_cash_account_with_currency",
                args: {
                    mode_of_payment: frm.doc.mode_of_payment_to,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message && r.message.account) {
                        frm.set_value("cash_account_to", r.message.account);
                        frm.set_value("cash_account_to_currency", r.message.currency);
                        frm.trigger("update_balance_to");
                        frm.trigger("validate_transfer_pair");
                        if (frm.doc.transaction_type === "Конвертация") {
                            frm.trigger("fetch_exchange_rate");
                            frm.trigger("calculate_conversion_amount");
                        }
                    } else {
                        frappe.msgprint(__("Для данного способа оплаты не настроен счет кассы для компании {0}", [frm.doc.company]));
                        frm.set_value("cash_account_to", "");
                        frm.set_value("cash_account_to_currency", "");
                        frm.set_value("balance_to", 0);
                    }
                }
            });
        } else {
            frm.set_value("cash_account_to", "");
            frm.set_value("cash_account_to_currency", "");
            frm.set_value("balance_to", 0);
        }
    },

    update_balance_to: function(frm) {
        if (frm.doc.cash_account_to && frm.doc.company) {
            frappe.call({
                method: "extra.extra.doctype.kassa.kassa.get_account_balance",
                args: {
                    account: frm.doc.cash_account_to,
                    company: frm.doc.company
                },
                callback: function(r) {
                    frm.set_value("balance_to", r.message || 0);
                }
            });
        }
    },

    set_mode_of_payment_query: function(frm) {
        frm.set_query("mode_of_payment", function() {
            if (frm.doc.transaction_type === "Конвертация") {
                return {
                    query: "extra.extra.doctype.kassa.kassa.conversion_mode_of_payment_query",
                    filters: {
                        company: frm.doc.company
                    }
                };
            }

            return {
                filters: {
                    enabled: 1
                }
            };
        });
    },

    set_mode_of_payment_to_query: function(frm) {
        frm.set_query("mode_of_payment_to", function() {
            if (frm.doc.transaction_type === "Конвертация") {
                return {
                    query: "extra.extra.doctype.kassa.kassa.conversion_mode_of_payment_query",
                    filters: {
                        company: frm.doc.company,
                        source_mode_of_payment: frm.doc.mode_of_payment
                    }
                };
            }

            if (frm.doc.transaction_type === "Перемещения") {
                return {
                    query: "extra.extra.doctype.kassa.kassa.transfer_mode_of_payment_query",
                    filters: {
                        company: frm.doc.company,
                        source_mode_of_payment: frm.doc.mode_of_payment
                    }
                };
            }

            let filters = {
                enabled: 1
            };
            if (frm.doc.mode_of_payment) {
                filters.name = ["!=", frm.doc.mode_of_payment];
            }
            return { filters: filters };
        });
    },

    validate_transfer_pair: function(frm) {
        if (!in_list(["Перемещения", "Конвертация"], frm.doc.transaction_type)) return;
        if (!frm.doc.cash_account_currency || !frm.doc.cash_account_to_currency) return;

        const sameCurrency = frm.doc.cash_account_currency === frm.doc.cash_account_to_currency;
        if (frm.doc.transaction_type === "Перемещения" && !sameCurrency) {
            frappe.msgprint({
                title: __("Ошибка валюты"),
                indicator: "red",
                message: __("Для перемещения способы оплаты должны иметь одинаковую валюту.")
            });
        }
        if (frm.doc.transaction_type === "Конвертация" && sameCurrency) {
            frappe.msgprint({
                title: __("Ошибка валюты"),
                indicator: "red",
                message: __("Для конвертации способы оплаты должны иметь разные валюты.")
            });
        }
    },

    fetch_exchange_rate: function(frm) {
        if (frm.doc.transaction_type !== "Конвертация") return;
        if (!frm.doc.cash_account_currency) return;

        const currencies = [
            frm.doc.cash_account_currency,
            frm.doc.cash_account_to_currency || get_default_target_currency(frm)
        ];
        const quoteCurrency = currencies.find(currency => currency && currency !== "USD");
        if (!quoteCurrency) return;

        frappe.call({
            method: "extra.extra.doctype.kassa.kassa.get_exchange_rate",
            args: {
                from_currency: "USD",
                to_currency: quoteCurrency,
                date: frm.doc.date || frappe.datetime.get_today()
            },
            callback: function(r) {
                if (r.message) {
                    frm.set_value("exchange_rate", r.message);
                }
            }
        });
    },

    date: function(frm) {
        if (frm.doc.transaction_type === "Конвертация") {
            frm.trigger("fetch_exchange_rate");
        }
    },

    debit_amount: function(frm) {
        if (frm.doc.transaction_type !== "Конвертация") return;
        set_derived_value(frm, "manual_credit_amount", 0);
        frm.trigger("calculate_conversion_amount");
    },

    exchange_rate: function(frm) {
        if (frm.doc.transaction_type !== "Конвертация") return;
        set_derived_value(frm, "manual_credit_amount", 0);
        frm.trigger("calculate_conversion_amount");
        frm.trigger("set_exchange_rate_description");
    },

    credit_amount: function(frm) {
        if (frm.doc.transaction_type !== "Конвертация") return;
        if (frm._setting_credit_amount_from_script) return;
        set_derived_value(
            frm,
            "manual_credit_amount",
            flt(frm.doc.credit_amount) > 0 ? 1 : 0
        );
    },

    calculate_conversion_amount: function(frm) {
        if (frm.doc.transaction_type !== "Конвертация") return;
        if (!frm.doc.debit_amount || !frm.doc.exchange_rate) return;
        if (cint(frm.doc.manual_credit_amount)) return;

        const sourceCurrency = frm.doc.cash_account_currency;
        const targetCurrency = frm.doc.cash_account_to_currency || get_default_target_currency(frm);
        const rate = flt(frm.doc.exchange_rate);
        let creditAmount = 0;

        // Kurs doim 1 USD = ? UZS ko'rinishida saqlanadi.
        if (sourceCurrency === "USD") {
            creditAmount = flt(frm.doc.debit_amount) * rate;
        } else if (sourceCurrency === "UZS") {
            creditAmount = flt(frm.doc.debit_amount) / rate;
        }

        const precision = targetCurrency === "UZS" ? 0 : 2;
        frm._setting_credit_amount_from_script = true;
        set_derived_value(frm, "credit_amount", flt(creditAmount, precision));
        frm._setting_credit_amount_from_script = false;
    },

    set_exchange_rate_description: function(frm) {
        if (frm.doc.transaction_type !== "Конвертация") return;

        const rate = flt(frm.doc.exchange_rate);
        if (!rate) {
            frm.set_df_property("exchange_rate", "description", "");
            return;
        }

        frm.set_df_property(
            "exchange_rate",
            "description",
            __("Обратный курс: 1 UZS = {0} USD", [format_number(1 / rate, null, 9)])
        );
    },

    update_balance_label: function(frm) {
        if (in_list(["Перемещения", "Конвертация"], frm.doc.transaction_type)) {
            frm.set_df_property("balance", "label", "Остаток (откуда)");
        } else {
            frm.set_df_property("balance", "label", "Остаток");
        }
        frm.refresh_field("balance");
    },

    update_party_type_options: function(frm) {
        let options = ["", "Customer", "Supplier", "Shareholder", "Employee", "Расходы"];

        if (frm.doc.transaction_type !== "Приход") {
            options.push("Дивиденд");
        }

        frm.set_df_property("party_type", "options", options.join("\n"));
        frm.refresh_field("party_type");

        if (frm.doc.docstatus === 0 && frm.doc.transaction_type === "Приход" && frm.doc.party_type === "Дивиденд") {
            frm.set_value("party_type", "");
            frappe.msgprint(__("Тип контрагента Дивиденд разрешен только для операции Расход."));
        }
    },

    party_type: function(frm) {
        frm.trigger("validate_dividend_transaction");
        frm.set_value("party", "");
        frm.set_value("expense_account", "");
        frm.set_value("party_name", "");
        frm.set_value("expense_account_name", "");

        if (frm.doc.party_type === "Расходы") {
            frm.set_df_property("expense_account", "reqd", 1);
            frm.set_df_property("party", "reqd", 0);
        } else if (frm.doc.party_type === "Дивиденд") {
            frm.set_df_property("expense_account", "reqd", 0);
            frm.set_df_property("party", "reqd", 0);
        } else if (frm.doc.party_type) {
            frm.set_df_property("expense_account", "reqd", 0);
            frm.set_df_property("party", "reqd", 1);
        } else {
            frm.set_df_property("expense_account", "reqd", 0);
            frm.set_df_property("party", "reqd", 0);
        }

        frm.refresh_fields();
    },

    party: function(frm) {
        if (frm.doc.party && frm.doc.party_type) {
            let name_field = get_party_name_field(frm.doc.party_type);
            if (name_field) {
                frappe.db.get_value(frm.doc.party_type, frm.doc.party, name_field, function(r) {
                    if (r && r[name_field]) {
                        frm.set_value("party_name", r[name_field]);
                    }
                });
            }

            if (in_list(["Customer", "Supplier"], frm.doc.party_type)) {
                frappe.call({
                    method: "extra.extra.doctype.kassa.kassa.get_party_currency",
                    args: {
                        party_type: frm.doc.party_type,
                        party: frm.doc.party,
                        company: frm.doc.company
                    },
                    callback: function(r) {
                        if (r.message) {
                            frm.set_value("party_currency", r.message);
                            frm.trigger("validate_currency");
                        }
                    }
                });
            }
        } else {
            frm.set_value("party_name", "");
            frm.set_value("party_currency", "");
        }
    },

    expense_account: function(frm) {
        if (frm.doc.expense_account) {
            frappe.db.get_value("Account", frm.doc.expense_account, "account_name", function(r) {
                if (r && r.account_name) {
                    frm.set_value("expense_account_name", r.account_name);
                }
            });
        } else {
            frm.set_value("expense_account_name", "");
        }
    },

    validate_currency: function(frm) {
        if (frm.doc.cash_account_currency && frm.doc.party_currency) {
            if (frm.doc.cash_account_currency !== frm.doc.party_currency) {
                frappe.validated = false;
                frappe.msgprint({
                    title: __("Ошибка валюты"),
                    indicator: "red",
                    message: __("Валюта кассы ({0}) не совпадает с валютой контрагента ({1}). Выберите соответствующий способ оплаты.",
                        [frm.doc.cash_account_currency, frm.doc.party_currency])
                });
            }
        }
    },

    validate_dividend_transaction: function(frm) {
        if (frm.doc.transaction_type === "Приход" && frm.doc.party_type === "Дивиденд") {
            frappe.validated = false;
            frappe.msgprint({
                title: __("Неверная комбинация"),
                indicator: "red",
                message: __("Тип контрагента Дивиденд разрешен только для операции Расход.")
            });
            if (frm.doc.docstatus === 0) {
                frm.set_value("party_type", "");
            }
        }
    },

    validate: function(frm) {
        frm.trigger("validate_dividend_transaction");
    }
});


function set_derived_value(frm, fieldname, value) {
    const current = frm.doc[fieldname] == null ? "" : frm.doc[fieldname];
    const next = value == null ? "" : value;
    if (current === next) return;

    frm.doc[fieldname] = value;
    frm.refresh_field(fieldname);
}

function get_default_target_currency(frm) {
    if (frm.doc.cash_account_currency === "USD") return "UZS";
    if (frm.doc.cash_account_currency === "UZS") return "USD";
    return "";
}

function get_party_name_field(party_type) {
    const name_fields = {
        "Customer": "customer_name",
        "Supplier": "supplier_name",
        "Shareholder": "title",
        "Employee": "employee_name"
    };
    return name_fields[party_type] || null;
}
