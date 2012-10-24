# ERPNext - web based ERP (http://erpnext.com)
# Copyright (C) 2012 Web Notes Technologies Pvt Ltd
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import webnotes
from webnotes.utils import flt, cstr, cint, round_doc
import webnotes.model.doctype
from webnotes.model.code import get_obj
from webnotes.model.utils import getlist
from webnotes.model.doc import Document
from utilities.transaction_base import TransactionBase
import json

class AccountsController(TransactionBase):
	def make_gl_entries(self, cancel=False, adv_adj=False, mapper=None,
			merge_entries=True,	update_outstanding='Yes', gl_map=None):
		"""make gl entries based on jv, invoice or stock valuation"""
		self.entries = []
		self.merged_entries = []
		self.total_debit = self.total_credit = 0.0
		
		if gl_map:
			self.entries = gl_map
		else:
			self.make_gl_map(mapper)

		self.merge_similar_entries(merge_entries)
		
		self.check_budget(cancel)
		self.save_entries(cancel, adv_adj, update_outstanding)

		if cancel:
			self.set_as_cancel()
		else:
			self.validate_total_debit_credit()
			
			
	def make_gl_map(self, mapper):
		def _gl_map(parent, d, entry_map):
			if self.get_val(entry_map['account'], d, parent) \
					and (self.get_val(entry_map['debit'], d, parent)
			 		or self.get_val(entry_map['credit'], d, parent)):
				gl_dict = {}
				for k in entry_map:
					gl_dict[k] = self.get_val(entry_map[k], d, parent)
				self.entries.append(gl_dict)
			
		# get entries
		gl_fields = ", ".join([d.fieldname for d in \
			webnotes.model.doctype.get("GL Mapper Detail").get({
			"doctype": "DocField", "parent": "GL Mapper Detail"})])
		entry_map_list = webnotes.conn.sql("""select %s from `tabGL Mapper Detail` 
			where parent = %s""" % (gl_fields, '%s'), mapper or self.doc.doctype, as_dict=1)
		
		for entry_map in entry_map_list:
			table_field = entry_map.get("table_field")
			
			# table_field does not exist in gl entry table
			entry_map.pop("table_field")
			
			if table_field:
				for d in getlist(self.doclist, table_field):
					# taxes_and_charges is the table of other charges in purchase cycle
					if table_field == "taxes_and_charges" and \
							d.fields.get("category") == "Valuation":
						# don't create gl entry for only valuation type charges
						continue
					_gl_map(self.doc, d, entry_map)
			else:
				_gl_map(None, self.doc, entry_map)
			
				
	def get_val(self, src, d, parent=None):
		"""Get field values from the voucher"""
		if not src:
			return None
		if src.startswith('parent:'):
			return parent.fields[src.split(':')[1]]
		elif src.startswith('value:'):
			return eval(src.split(':')[1])
		elif src:
			return d.fields.get(src)
				
				
	def merge_similar_entries(self, merge_entries):
		if merge_entries:
			for entry in self.entries:
				# if there is already an entry in this account then just add it 
				# to that entry
				same_head = self.check_if_in_list(entry)
				if same_head:
					same_head['debit']	= flt(same_head['debit']) + flt(entry['debit'])
					same_head['credit'] = flt(same_head['credit']) + flt(entry['credit'])
				else:
					self.merged_entries.append(entry)
		else:
			self.merged_entries = self.entries

	
	def save_entries(self, cancel, adv_adj, update_outstanding):
		def _swap(gle):
			gle.debit, gle.credit = abs(flt(gle.credit)), abs(flt(gle.debit))
		
		for entry in self.merged_entries:
			gle = Document('GL Entry', fielddata=entry)
			
			# toggle debit, credit if negative entry
			if flt(gle.debit) < 0 or flt(gle.credit) < 0:
				_swap(gle)

			# toggled debit/credit in two separate condition because 
			# both should be executed at the 
			# time of cancellation when there is negative amount (tax discount)
			if cancel:
				_swap(gle)

			gle_obj = get_obj(doc=gle)
			# validate except on_cancel
			if not cancel:
				gle_obj.validate()

			# save
			gle.save(1)
			gle_obj.on_update(adv_adj, cancel, update_outstanding)

			# update total debit / credit
			self.total_debit += flt(gle.debit)
			self.total_credit += flt(gle.credit)

	def check_if_in_list(self, gle):
		for e in self.merged_entries:
			if e['account'] == gle['account'] and \
					cstr(e.get('against_voucher'))==cstr(gle.get('against_voucher')) \
					and cstr(e.get('against_voucher_type')) == \
						cstr(gle.get('against_voucher_type')) \
					and cstr(e.get('cost_center')) == cstr(gle.get('cost_center')):
				return e
			
	def validate_total_debit_credit(self):
		if abs(self.total_debit - self.total_credit) > 0.005:
			msgprint("""Debit and Credit not equal for this voucher: Diff (Debit) is %s""" %
			 	(self.total_debit - self.total_credit), raise_exception=1)
		
	def set_as_cancel(self):
		webnotes.conn.sql("""update `tabGL Entry` set is_cancelled='Yes' 
			where voucher_type=%s and voucher_no=%s""", (self.doc.doctype, self.doc.name))
		
	def check_budget(self, cancel):
		for gle in self.merged_entries:
			if gle.get('cost_center'):
				#check budget only if account is expense account
				acc_details = webnotes.conn.get_value("Account", gle['account'], 
					['is_pl_account', 'debit_or_credit'])
			
				if acc_details[0]=="Yes" and acc_details[1]=="Debit":
					get_obj('Budget Control').check_budget(gle, cancel)
		
	def get_gl_dict(self, args, cancel):
		"""this method populates the common properties of a gl entry record"""
		gl_dict = {
			'company': self.doc.company, 
			'posting_date': self.doc.posting_date,
			'voucher_type': self.doc.doctype,
			'voucher_no': self.doc.name,
			'aging_date': self.doc.fields.get("aging_date") or self.doc.posting_date,
			'remarks': self.doc.remarks,
			'is_cancelled': cancel and "Yes" or "No",
			'fiscal_year': self.doc.fiscal_year,
			'debit': 0,
			'credit': 0,
			'is_opening': self.doc.fields.get("is_opening") or "No",
		}
		gl_dict.update(args)
		return gl_dict
	
	def get_company_details(self):
		abbr, stock_in_hand = webnotes.conn.get_value("Company", self.doc.company,
			["abbr", "stock_in_hand"])
		
		if not stock_in_hand:
			webnotes.msgprint("""Please specify "Stock In Hand" account 
				for company: %s""" % (self.doc.company,), raise_exception=1)
				
		return abbr, stock_in_hand
		
	def calculate_taxes_and_totals(self):
		"""
			Calculates:
				* amount for each item
				* item_tax_amount for each item, 
				* tax amount and tax total for each tax
				* net total
				* total taxes
				* grand total
		"""
		self.prepare_precision_maps()
		self.item_doclist = self.doclist.get({"parentfield": self.fname})
		self.tax_doclist = self.doclist.get({"parentfield": self.taxes_and_charges})

		self.calculate_net_total()
		self.initialize_taxes()
			
		self.calculate_taxes()
		self.calculate_totals()
			
	def calculate_net_total(self):
		self.doc.net_total = 0
		for item in self.item_doclist:
			# round relevant values
			round_doc(item, self.item_precision)
			
			# calculate amount and net total
			item.amount = flt((item.qty * item.rate) - \
				((item.discount / 100.0) * item.rate), self.item_precision["amount"])
			self.doc.net_total += item.amount
			
		self.doc.net_total = flt(self.doc.net_total, self.main_precision["net_total"])
		
	def initialize_taxes(self):
		for tax in self.tax_doclist:
			# initialize totals to 0
			tax.tax_amount = tax.total = 0
			tax.grand_total_for_current_item = tax.tax_amount_for_current_item = 0
			tax.item_wise_tax_detail = {}
			
			# round relevant values
			round_doc(tax, self.tax_precision)
			
	def calculate_taxes(self):
		def _load_item_tax_rate(item_tax_rate):
			if not item_tax_rate:
				return {}

			return json.loads(item_tax_rate)

		def _get_tax_rate(item_tax_map, tax):
			if item_tax_map.has_key(tax.account_head):
				return flt(item_tax_map.get(tax.account_head), self.tax_precision["rate"])
			else:
				return tax.rate
		
		def _get_current_tax_amount(item, tax, item_tax_map):
			tax_rate = _get_tax_rate(item_tax_map, tax)
	
			if tax.charge_type == "Actual":
				# distribute the tax amount proportionally to each item row
				current_tax_amount = (self.doc.net_total
					and ((item.amount / self.doc.net_total) * tax.rate)
					or 0)
			elif tax.charge_type == "On Net Total":
				current_tax_amount = (tax_rate / 100.0) * item.amount
			elif tax.charge_type == "On Previous Row Amount":
				current_tax_amount = (tax_rate / 100.0) * \
					self.tax_doclist[cint(tax.row_id) - 1].tax_amount_for_current_item
			elif tax.charge_type == "On Previous Row Total":
				current_tax_amount = (tax_rate / 100.0) * \
					self.tax_doclist[cint(tax.row_id) - 1].grand_total_for_current_item
	
			return flt(current_tax_amount, self.tax_precision["tax_amount"])
		
		# build is_stock_item_map
		item_codes = list(set(item.item_code for item in self.item_doclist))
		is_stock_item_map = dict(webnotes.conn.sql("""select name, 
			ifnull(is_stock_item, "No") from `tabItem` where name in (%s)""" % \
			(", ".join((["%s"]*len(item_codes))),),
			item_codes))
		
		# loop through items and set item tax amount
		for item in self.item_doclist:
			item_tax_map = _load_item_tax_rate(item.item_tax_rate)
			if not item.item_tax_amount:
				item.item_tax_amount = 0
			
			for i, tax in enumerate(self.tax_doclist):
				# tax_amount represents the amount of tax for the current step
				current_tax_amount = _get_current_tax_amount(item, tax, item_tax_map)
				
				if self.transaction_type == "Purchase" and \
						tax.category in ["Valuation", "Valuation and Total"] and \
						is_stock_item_map.get(item.item_code)=="Yes":
					item.item_tax_amount += current_tax_amount
				
				# case when net total is 0 but there is an actual type charge
				# in this case add the actual amount to tax.tax_amount
				# and tax.grand_total_for_current_item for the first such iteration
				zero_net_total_adjustment = 0
				if not (current_tax_amount or self.doc.net_total or tax.tax_amount) and \
						tax.charge_type=="Actual":
					zero_net_total_adjustment = flt(tax.rate, self.tax_precision["tax_amount"])
				
				# store tax_amount for current item as it will be used for
				# charge type = 'On Previous Row Amount'
				tax.tax_amount_for_current_item = current_tax_amount + \
					zero_net_total_adjustment
				
				# accumulate tax amount into tax.tax_amount
				tax.tax_amount += tax.tax_amount_for_current_item
				
				if self.transaction_type == "Purchase" and tax.category == "Valuation":
					# if just for valuation, do not add the tax amount in total
					# hence, setting it as 0 for further steps
					current_tax_amount = zero_net_total_adjustment = 0
				
				# Calculate tax.total viz. grand total till that step
				# note: grand_total_for_current_item contains the contribution of 
				# item's amount, previously applied tax and the current tax on that item
				if i==0:
					tax.grand_total_for_current_item = flt(item.amount +
						current_tax_amount + zero_net_total_adjustment, 
						self.tax_precision["total"])
				else:
					tax.grand_total_for_current_item = \
						flt(self.tax_doclist[i-1].grand_total_for_current_item +
							current_tax_amount + zero_net_total_adjustment,
							self.tax_precision["total"])
				
				# prepare itemwise tax detail
				tax.item_wise_tax_detail[item.item_code] = current_tax_amount
				
				# in tax.total, accumulate grand total of each item
				tax.total += tax.grand_total_for_current_item
		
		# QUESTION: is this necessary?
		# store itemwise tax details as a json
		for tax in self.tax_doclist:
			tax.item_wise_tax_detail = json.dumps(tax.item_wise_tax_detail)
			# print tax.item_wise_tax_detail
		
		# TODO: remove this once new doc.py and controller.py are implemented
		# remove grand_total_for_current_item
		for tax in self.tax_doclist:
			del tax.fields["grand_total_for_current_item"]
			del tax.fields["tax_amount_for_current_item"]
			
	def calculate_totals(self):
		if self.tax_doclist:
			self.doc.grand_total = self.tax_doclist[-1].total
		else:
			self.doc.grand_total = self.doc.net_total
		
		self.doc.grand_total = flt(self.doc.grand_total,
			self.main_precision["grand_total"])
		
		self.doc.fields[self.taxes_and_charges_total] = \
			flt(self.doc.grand_total - self.doc.net_total,
			self.main_precision[self.taxes_and_charges_total])
		
		# self.doc.rounded_total = round(self.doc.grand_total)
		
		# TODO: calculate import / export values
			
	def prepare_precision_maps(self):
		doctypelist = webnotes.model.doctype.get(self.doc.doctype)
		self.main_precision = doctypelist.get_precision_map()
		self.item_precision = doctypelist.get_precision_map(parentfield=self.fname)
		self.tax_precision = \
			doctypelist.get_precision_map(parentfield=self.taxes_and_charges)