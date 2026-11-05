from odoo import api, fields, models


class Account_FollowupManual_Reminder(models.TransientModel):
    _name = 'account_followup.manual_reminder'
    _inherit = ['mail.composer.mixin']
    _description = "Wizard for sending manual reminders to clients"

    move_ids = fields.Many2many(comodel_name='account.move')
    preview_subject = fields.Char('Subject Preview', compute='_compute_preview_fields')
    preview_body = fields.Html('Body Preview', compute='_compute_preview_fields')
    alerts = fields.Json(compute='_compute_alerts')

    @api.depends('template_id', 'move_ids')
    def _compute_preview_fields(self):
        """Render template preview with selected customer as sample.
        If several customers are selected, the first one is used as preview
        record and the due amount for that customer is injected in context,
        similarly to the actual send flow.
        """
        for wizard in self:
            if not wizard.template_id or not wizard.move_ids:
                wizard.preview_subject = False
                wizard.preview_body = False
                continue

            partner = wizard.move_ids[:1].partner_id or self.env.user.partner_id
            partner_moves = wizard.move_ids.filtered(lambda m: m.partner_id == partner)
            # we account for possibility that the invoices are in a different currency from company's or there's invoices with differing currencies
            overdue_currency = partner.currency_id if len(partner_moves.currency_id) > 1 else wizard.move_ids[0].currency_id
            amount_property = 'amount_residual_signed' if len(partner_moves.currency_id) > 1 else 'amount_residual'
            amount_due = sum(partner_moves.mapped(amount_property))
            values = wizard.template_id.with_context(amount_due=amount_due, overdue_currency=overdue_currency)._generate_template(
                [partner.id],
                ['subject', 'body_html'],
            )[partner.id]
            wizard.preview_subject = values.get('subject')
            wizard.preview_body = values.get('body_html')

    @api.depends('move_ids')
    def _compute_alerts(self):
        for wizard in self:
            alerts = {}
            partners_without_mail = wizard.move_ids.partner_id.filtered(lambda p: not p.email)

            if partners_without_mail:
                alerts['account_missing_email'] = {
                    'level': 'warning',
                    'message': self.env._("Partner(s) should have an email address."),
                    'action_text': self.env._("View Partner(s)"),
                    'action': (
                        partners_without_mail._get_records_action(name=self.env._("Check Partner(s) Email(s)"))
                    ),
                }
            wizard.alerts = alerts

    def send_reminder(self):
        self.ensure_one()

        partner_data = self.env['account.move']._read_group(
            domain=[('id', 'in', self.move_ids.ids)],
            aggregates=['amount_residual:sum', 'amount_residual_signed:sum', 'id:recordset'],
            groupby=['partner_id'],
        )

        for partner, amount_residual, amount_residual_signed, moves in partner_data:
            overdue_currency = partner.currency_id if len(moves.currency_id) > 1 else moves[0].currency_id
            amount_due = amount_residual_signed if len(moves.currency_id) > 1 else amount_residual

            # Render template per partner with their specific context
            template_ctx = self.template_id.with_context(
                amount_due=amount_due,
                overdue_currency=overdue_currency
            )
            body = template_ctx._render_field('body_html', [partner.id])[partner.id]

            attachment_ids = moves.invoice_pdf_report_id.ids
            followup_report = partner._get_followup_report(options=None)
            attachment_ids = attachment_ids + [followup_report]
            partner.message_post(
                body=body,
                partner_ids=[partner.id],
                attachment_ids=attachment_ids,
            )

        self.move_ids.last_reminder = fields.Date.context_today(self)
