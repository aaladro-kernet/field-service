# Copyright (C) 2019 Brian McMaster
# Copyright (C) 2019 Open Source Integrators
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    fsm_location_id = fields.Many2one(
        "fsm.location",
        string="Service Location",
        help="SO Lines generating a FSM order will be for this location",
    )
    fsm_order_ids = fields.Many2many(
        "fsm.order",
        compute="_compute_fsm_order_ids",
        string="Field Service orders associated to this sale",
    )
    fsm_order_count = fields.Integer(
        string="FSM Orders", compute="_compute_fsm_order_ids"
    )

    @api.depends("order_line")
    def _compute_fsm_order_ids(self):
        for order in self:
            orders = self.env["fsm.order"]
            orders |= self.env["fsm.order"].search(
                [("sale_line_id", "in", order.order_line.ids)]
            )
            orders |= self.env["fsm.order"].search([("sale_id", "=", order.id)])
            order.fsm_order_ids = orders
            order.fsm_order_count = len(order.fsm_order_ids)

    @api.onchange("partner_id")
    def onchange_partner_id(self):
        """
        Autofill the Sale Order's FS location with the partner_id,
        the partner_shipping_id or the partner_id.commercial_partner_id if
        they are FS locations.
        """
        super(SaleOrder, self).onchange_partner_id()
        domain = [
            "|",
            "|",
            ("partner_id", "=", self.partner_id.id),
            ("partner_id", "=", self.partner_shipping_id.id),
            ("partner_id", "=", self.partner_id.commercial_partner_id.id),
        ]
        if self.partner_id.fsm_location:
            domain = [("partner_id", "=", self.partner_id.id)]
        location_ids = self.env["fsm.location"].search(domain)
        self.fsm_location_id = location_ids and location_ids[0] or False

    def _field_create_fsm_order_prepare_values(self):
        self.ensure_one()
        lines = self.order_line.filtered(
            lambda sol: sol.product_id.field_service_tracking == "sale"
        )
        templates = lines.mapped("product_id.fsm_order_template_id")
        note = ""
        hours = 0.0
        categories = self.env["fsm.category"]
        for template in templates:
            note += template.instructions or ""
            hours += template.duration
            categories |= template.category_ids
        return {
            "location_id": self.fsm_location_id.id,
            "location_directions": self.fsm_location_id.direction,
            "request_early": self.expected_date,
            "scheduled_date_start": self.expected_date,
            "todo": note,
            "category_ids": [(6, 0, categories.ids)],
            "scheduled_duration": hours,
            "sale_id": self.id,
            "company_id": self.company_id.id,
        }

    def _field_create_fsm_order(self):
        """Generate fsm_order for the given Sale Order, and link it.
        :return a mapping with the sale order id and its linked fsm_order
        :rtype dict
        """
        result = {}
        for so in self:
            # create fsm_order
            values = so._field_create_fsm_order_prepare_values()
            fsm_order = self.env["fsm.order"].sudo().create(values)
            # post message on SO
            msg_body = (
                _(
                    """Field Service Order Created: <a href=
                   # data-oe-model=fsm.order data-oe-id=%d>%s</a>
                """
                )
                % (fsm_order.id, fsm_order.name)
            )
            so.message_post(body=msg_body)
            # post message on fsm_order
            fsm_order_msg = (
                _(
                    """This order has been created from: <a href=
                   # data-oe-model=sale.order data-oe-id=%d>%s</a>
                """
                )
                % (so.id, so.name)
            )
            fsm_order.message_post(body=fsm_order_msg)
            result[so.id] = fsm_order
        return result

    def _field_find_fsm_order(self):
        """Find the fsm_order generated by the Sale Order. If no fsm_order
        linked, it will be created automatically.
        :return a mapping with the so line id and its linked fsm_order
        :rtype dict
        """
        # one search for all Sale Orders
        fsm_orders = self.env["fsm.order"].search(
            [("sale_id", "in", self.ids), ("sale_line_id", "=", False)]
        )
        fsm_order_mapping = {
            fsm_order.sale_id.id: fsm_order for fsm_order in fsm_orders
        }
        result = {}
        for so in self:
            # If the SO was confirmed, cancelled, set to draft then confirmed,
            # avoid creating a new fsm_order.
            fsm_order = fsm_order_mapping.get(so.id)
            # If not found, create one fsm_order for the so
            if not fsm_order:
                fsm_order = so._field_create_fsm_order()[so.id]
            result[so.id] = fsm_order
        return result

    def _action_confirm(self):
        """ On SO confirmation, some lines generate field service orders. """
        result = super(SaleOrder, self)._action_confirm()
        if any(
            sol.product_id.field_service_tracking != "no" for sol in self.order_line
        ):
            if not self.fsm_location_id:
                raise ValidationError(_("FSM Location must be set"))
            self.order_line._field_service_generation()
        return result

    def action_view_fsm_order(self):
        fsm_orders = self.mapped("fsm_order_ids")
        action = self.env.ref("fieldservice.action_fsm_dash_order").read()[0]
        if len(fsm_orders) > 1:
            action["domain"] = [("id", "in", fsm_orders.ids)]
        elif len(fsm_orders) == 1:
            action["views"] = [(self.env.ref("fieldservice.fsm_order_form").id, "form")]
            action["res_id"] = fsm_orders.id
        else:
            action = {"type": "ir.actions.act_window_close"}
        return action
