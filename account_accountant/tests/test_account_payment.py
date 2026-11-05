from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestAccountBillPayment(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.bank_journal_1 = cls.company_data['default_journal_bank']

    def test_bill_state_change_on_payment_state(self):
        """Test that bill payment state changes correctly when payment state transitions occur.
        • Draft payment case: Bill state reverts to 'not_paid' when payment is drafted
        • Payment unlink case: Bill state reverts to 'not_paid' when payment is deleted
        """
        bill = self.init_invoice('in_invoice', post=True, partner=self.partner_a, products=self.product_a)

        # We have to test it without any Outstanding Payment account set in Journal
        self.bank_journal_1.outbound_payment_method_line_ids.payment_account_id = False

        payment = self.env['account.payment.register']\
            .with_context(active_model='account.move', active_ids=bill.ids)\
            .create({})\
            ._create_payments()
        self.assertEqual(bill.payment_state, self.env['account.move']._get_invoice_in_payment_state())

        payment.action_draft()
        self.assertEqual(payment.state, 'draft')
        # With accountant module is installed, payment has no move_id, so bill goes to 'not_paid'.
        # Without it, payment has move_id (journal entry), so bill stays 'paid'.
        accountant_module = self.env['ir.module.module'].search([
            ('name', '=', 'accountant'), ('state', '=', 'installed')])
        expected_state = 'not_paid' if accountant_module else 'paid'
        self.assertEqual(payment.invoice_ids.payment_state, expected_state)

        payment.unlink()
        self.assertEqual(bill.payment_state, 'not_paid')

    def test_partial_reconcile_with_payments_coming_from_provider(self):
        """ Test that we not allow partial reconcile on payments coming from
            payment provider.
        """
        if 'payment_transaction_id' not in self.env['account.payment']._fields:
            self.skipTest("account_payment is not installed")

        provider = self.env['payment.provider'].create({  # noqa: OLS03001
            'name': 'Test',
            'journal_id': self.company_data['default_journal_bank'].id,
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_a.id,
            'journal_id': self.company_data['default_journal_sale'].id,
            'invoice_line_ids': [
                Command.create({
                    'account_id': self.company_data['default_account_revenue'].id,
                    'product_id': self.product_a.id,
                    'quantity': 1,
                    'price_unit': 100,
                })
            ]
        })
        payment_transaction = self.env['payment.transaction'].create({  # noqa: OLS03001
            'provider_id': provider.id,
            'payment_method_id': self.env.ref('payment.payment_method_unknown').id,
            'invoice_ids': [invoice.id],
            'partner_id': self.partner_a.id,
            'amount': 200,
            'currency_id': invoice.currency_id.id,
        })
        payment_transaction._create_payment(payment_method_line_id=self.company_data['default_journal_bank'].inbound_payment_method_line_ids[0].id)
        payment_transaction._post_process()
        payment = payment_transaction.payment_id
        invoice.action_post()
        payment.action_post()
        statement_line = self.env['account.bank.statement.line'].create({
            'journal_id': self.company_data['default_journal_bank'].id,
            'date': '2026-01-01',
            'partner_id': self.partner_a.id,
            'amount': 100.0,
        })
        inv_line = payment.move_id.line_ids.filtered(lambda l: l.balance == 200)
        self.assertFalse(statement_line._get_partial_amounts(-200, inv_line, -100, -100))
