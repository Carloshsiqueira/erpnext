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

from webnotes.utils import add_days, cint, cstr, date_diff, flt, get_defaults, getdate, nowdate, sendmail 
from webnotes.model.doc import Document, addchild
from webnotes.model.utils import getlist
from webnotes.model.code import get_obj
from webnotes import msgprint
from webnotes.utils.email_lib import sendmail
from utilities.transaction_base import TransactionBase

class DocType:
	def __init__(self,d,dl):
		self.doc, self.doclist = d, dl

	def get_company_currency(self,arg=''):
		dcc = TransactionBase().get_company_currency(arg)
		return dcc

	def get_period_difference(self,arg, cost_center =''):
		# used in General Ledger Page Report
		# used for Budget where cost center passed as extra argument
		acc, f, t = arg.split('~~~')
		c, fy = '', get_defaults()['fiscal_year']

		det = webnotes.conn.sql("select debit_or_credit, lft, rgt, is_pl_account from tabAccount where name=%s", acc)
		if f: c += (' and t1.posting_date >= "%s"' % f)
		if t: c += (' and t1.posting_date <= "%s"' % t)
		if cost_center: c += (' and t1.cost_center = "%s"' % cost_center)
		bal = webnotes.conn.sql("select sum(ifnull(t1.debit,0))-sum(ifnull(t1.credit,0)) from `tabGL Entry` t1 where t1.account='%s' %s" % (acc, c))
		bal = bal and flt(bal[0][0]) or 0

		if det[0][0] != 'Debit':
			bal = (-1) * bal

		return flt(bal)


	# Add a new account
	# -----------------
	def add_ac(self,arg):
		arg = eval(arg)
		ac = Document('Account')
		for d in arg.keys():
			ac.fields[d] = arg[d]
		ac.old_parent = ''
		ac_obj = get_obj(doc=ac)
		ac_obj.doc.freeze_account='No'
		ac_obj.validate()
		ac_obj.doc.save(1)
		ac_obj.on_update()

		return ac_obj.doc.name

	# Add a new cost center
	#----------------------
	def add_cc(self,arg):
		arg = eval(arg)
		cc = Document('Cost Center')
		# map fields
		for d in arg.keys():
			cc.fields[d] = arg[d]
		# map company abbr
		other_info = webnotes.conn.sql("select company_abbr from `tabCost Center` where name='%s'"%arg['parent_cost_center'])
		cc.company_abbr = other_info and other_info[0][0] or arg['company_abbr']

		cc_obj = get_obj(doc=cc)
		cc_obj.validate()
		cc_obj.doc.save(1)
		cc_obj.on_update()

		return cc_obj.doc.name


	def get_advances(self, obj, account_head, table_name,table_field_name, dr_or_cr):
		jv_detail = webnotes.conn.sql("""
			select 
				t1.name, t1.remark, t2.%s, t2.name
			from 
				`tabJournal Voucher` t1, `tabJournal Voucher Detail` t2 
			where 
				t1.name = t2.parent and (t2.against_voucher is null or t2.against_voucher = '')
				and (t2.against_invoice is null or t2.against_invoice = '') 
				and (t2.against_jv is null or t2.against_jv = '') 
				and t2.account = '%s' and t2.is_advance = 'Yes' and t1.docstatus = 1 
			order by t1.voucher_date 
		""" % (dr_or_cr,account_head))
			
		# clear advance table
		obj.doclist = obj.doc.clear_table(obj.doclist,table_field_name)
		# Create advance table
		for d in jv_detail:
			add = addchild(obj.doc, table_field_name, table_name, 1, obj.doclist)
			add.journal_voucher = d[0]
			add.jv_detail_no = d[3]
			add.remarks = d[1]
			add.advance_amount = flt(d[2])
			add.allocate_amount = 0
				
		return obj.doclist

	# Clear rows which is not adjusted
	#-------------------------------------
	def clear_advances(self, obj,table_name,table_field_name):
		for d in getlist(obj.doclist,table_field_name):
			if not flt(d.allocated_amount):
				webnotes.conn.sql("update `tab%s` set parent = '' where name = '%s' and parent = '%s'" % (table_name, d.name, d.parent))
				d.parent = ''

	# Update aginst document in journal voucher
	#------------------------------------------
	def update_against_document_in_jv(self, obj, table_field_name, against_document_no, against_document_doctype, account_head, dr_or_cr,doctype):
		for d in getlist(obj.doclist, table_field_name):
			self.validate_jv_entry(d, account_head, dr_or_cr)
			if flt(d.advance_amount) == flt(d.allocated_amount):
				# cancel JV
				jv_obj = get_obj('Journal Voucher', d.journal_voucher, with_children=1)
				get_obj(dt='GL Control').make_gl_entries(jv_obj.doc, jv_obj.doclist, cancel =1, adv_adj =1)

				# update ref in JV Detail
				webnotes.conn.sql("update `tabJournal Voucher Detail` set %s = '%s' where name = '%s'" % (doctype=='Purchase Invoice' and 'against_voucher' or 'against_invoice', cstr(against_document_no), d.jv_detail_no))

				# re-submit JV
				jv_obj = get_obj('Journal Voucher', d.journal_voucher, with_children =1)
				get_obj(dt='GL Control').make_gl_entries(jv_obj.doc, jv_obj.doclist, cancel = 0, adv_adj =1)

			elif flt(d.advance_amount) > flt(d.allocated_amount):
				# cancel JV
				jv_obj = get_obj('Journal Voucher', d.journal_voucher, with_children=1)
				get_obj(dt='GL Control').make_gl_entries(jv_obj.doc, jv_obj.doclist, cancel =1, adv_adj = 1)

				# add extra entries
				self.add_extra_entry(jv_obj, d.journal_voucher, d.jv_detail_no, flt(d.allocated_amount), account_head, doctype, dr_or_cr, against_document_no)

				# re-submit JV
				jv_obj = get_obj('Journal Voucher', d.journal_voucher, with_children =1)
				get_obj(dt='GL Control').make_gl_entries(jv_obj.doc, jv_obj.doclist, cancel = 0, adv_adj = 1)
			else:
				msgprint("Allocation amount cannot be greater than advance amount")
				raise Exception
				

	# Add extra row in jv detail for unadjusted amount
	#--------------------------------------------------
	def add_extra_entry(self,jv_obj,jv,jv_detail_no, allocate, account_head, doctype, dr_or_cr, against_document_no):
		# get old entry details

		jvd = webnotes.conn.sql("select %s, cost_center, balance, against_account from `tabJournal Voucher Detail` where name = '%s'" % (dr_or_cr,jv_detail_no))
		advance = jvd and flt(jvd[0][0]) or 0
		balance = flt(advance) - flt(allocate)

		# update old entry
		webnotes.conn.sql("update `tabJournal Voucher Detail` set %s = '%s', %s = '%s' where name = '%s'" % (dr_or_cr, flt(allocate), doctype == "Purchase Invoice" and 'against_voucher' or 'against_invoice',cstr(against_document_no), jv_detail_no))

		# new entry with balance amount
		add = addchild(jv_obj.doc, 'entries', 'Journal Voucher Detail', 1, jv_obj.doclist)
		add.account = account_head
		add.cost_center = cstr(jvd[0][1])
		add.balance = cstr(jvd[0][2])
		add.fields[dr_or_cr] = balance
		add.against_account = cstr(jvd[0][3])
		add.is_advance = 'Yes'
		add.save(1)
		
	# check if advance entries are still valid
	# ----------------------------------------
	def validate_jv_entry(self, d, account_head, dr_or_cr):
		# 1. check if there is already a voucher reference
		# 2. check if amount is same
		# 3. check if is_advance is 'Yes'
		# 4. check if jv is submitted
		ret = webnotes.conn.sql("select t2.%s from `tabJournal Voucher` t1, `tabJournal Voucher Detail` t2 where t1.name = t2.parent and ifnull(t2.against_voucher, '') = '' and ifnull(t2.against_invoice, '') = '' and t2.account = '%s' and t1.name = '%s' and t2.name = '%s' and t2.is_advance = 'Yes' and t1.docstatus=1 and t2.%s = %s" % (dr_or_cr, account_head, d.journal_voucher, d.jv_detail_no, dr_or_cr, d.advance_amount))
		if (not ret):
			msgprint("Please click on 'Get Advances Paid' button as the advance entries have been changed.")
			raise Exception
		return


######################################################################################################################

	#------------------------------------------
	def reconcile_against_document(self, args):
		"""
			Cancel JV, Update aginst document, split if required and resubmit jv
		"""
		
		for d in args:
			self.check_if_jv_modified(d)

			against_fld = {
				'Journal Voucher' : 'against_jv',
				'Sales Invoice' : 'against_invoice',
				'Purchase Invoice' : 'against_voucher'
			}
			
			d['against_fld'] = against_fld[d['against_voucher_type']]

			# cancel JV
			jv_obj = get_obj('Journal Voucher', d['voucher_no'], with_children=1)
			self.make_gl_entries(jv_obj.doc, jv_obj.doclist, cancel =1, adv_adj =1)

			# update ref in JV Detail
			self.update_against_doc(d, jv_obj)

			# re-submit JV
			jv_obj = get_obj('Journal Voucher', d['voucher_no'], with_children =1)
			self.make_gl_entries(jv_obj.doc, jv_obj.doclist, cancel = 0, adv_adj =1)

	#------------------------------------------
	def update_against_doc(self, d, jv_obj):
		"""
			Updates against document, if partial amount splits into rows
		"""

		webnotes.conn.sql("""
			update `tabJournal Voucher Detail` t1, `tabJournal Voucher` t2	
			set t1.%(dr_or_cr)s = '%(allocated_amt)s', t1.%(against_fld)s = '%(against_voucher)s', t2.modified = now() 
			where t1.name = '%(voucher_detail_no)s' and t1.parent = t2.name""" % d)

		if d['allocated_amt'] < d['unadjusted_amt']:
			jvd = webnotes.conn.sql("select cost_center, balance, against_account, is_advance from `tabJournal Voucher Detail` where name = '%s'" % d['voucher_detail_no'])
			# new entry with balance amount
			ch = addchild(jv_obj.doc, 'entries', 'Journal Voucher Detail', 1)
			ch.account = d['account']
			ch.cost_center = cstr(jvd[0][0])
			ch.balance = cstr(jvd[0][1])
			ch.fields[d['dr_or_cr']] = flt(d['unadjusted_amt']) - flt(d['allocated_amt'])
			ch.fields[d['dr_or_cr']== 'debit' and 'credit' or 'debit'] = 0
			ch.against_account = cstr(jvd[0][2])
			ch.is_advance = cstr(jvd[0][3])
			ch.docstatus = 1
			ch.save(1)

	#------------------------------------------
	def check_if_jv_modified(self, args):
		"""
			check if there is already a voucher reference
			check if amount is same
			check if jv is submitted
		"""
		ret = webnotes.conn.sql("""
			select t2.%(dr_or_cr)s from `tabJournal Voucher` t1, `tabJournal Voucher Detail` t2 
			where t1.name = t2.parent and t2.account = '%(account)s' 
			and ifnull(t2.against_voucher, '')='' and ifnull(t2.against_invoice, '')='' and ifnull(t2.against_jv, '')=''
			and t1.name = '%(voucher_no)s' and t2.name = '%(voucher_detail_no)s'
			and t1.docstatus=1 and t2.%(dr_or_cr)s = %(unadjusted_amt)s
		""" % (args))
		
		if not ret:
			msgprint("Payment Entry has been modified after you pulled it. Please pull it again.", raise_exception=1)
		

	def repost_illegal_cancelled(self, after_date='2011-01-01'):
		"""
			Find vouchers that are not cancelled correctly and repost them
		"""
		vl = webnotes.conn.sql("""
			select voucher_type, voucher_no, account, sum(debit) as sum_debit, sum(credit) as sum_credit
			from `tabGL Entry`
			where is_cancelled='Yes' and creation > %s
			group by voucher_type, voucher_no, account
			""", after_date, as_dict=1)

		ac_list = []
		for v in vl:
			if v['sum_debit'] != 0 or v['sum_credit'] != 0:
				ac_list.append(v['account'])

		fy_list = webnotes.conn.sql("""select name from `tabFiscal Year`
		where (%s between year_start_date and date_sub(date_add(year_start_date,interval 1 year), interval 1 day))
		or year_start_date > %s
		order by year_start_date ASC""", (after_date, after_date))

		for fy in fy_list:
			fy_obj = get_obj('Fiscal Year', fy[0])
			for a in set(ac_list):
				fy_obj.repost(a)


def manage_recurring_invoices():
	""" 
		Create recurring invoices on specific date by copying the original one
		and notify the concerned people
	"""	
	rv = webnotes.conn.sql("""select name, recurring_id from `tabSales Invoice` \
		where ifnull(convert_into_recurring_invoice, 0) = 1 and next_date = %s \
		and next_date <= ifnull(end_date, '2199-12-31') and docstatus=1""", nowdate())
	
	
	exception_list = []
	for d in rv:
		if not webnotes.conn.sql("""select name from `tabSales Invoice` \
			where posting_date = %s and recurring_id = %s and docstatus=1""", (nowdate(), d[1])):
			try:
				prev_rv = get_obj('Sales Invoice', d[0], with_children=1)
				new_rv = create_new_invoice(prev_rv)

				send_notification(new_rv)
				webnotes.conn.commit()
			except Exception, e:
				webnotes.conn.rollback()

				webnotes.conn.begin()
				webnotes.conn.sql("update `tabSales Invoice` set \
					convert_into_recurring_invoice = 0 where name = %s", d[0])
				notify_errors(d[0], prev_rv.doc.owner)
				webnotes.conn.commit()

				exception_list.append(e)
			finally:
				webnotes.conn.begin()
			
	if exception_list:
		exception_message = "\n\n".join([cstr(d) for d in exception_list])
		raise Exception, exception_message
		
		
def notify_errors(inv, owner):
	import webnotes
	import website
		
	exception_msg = """
		Dear User,

		An error occured while creating recurring invoice from %s (at %s).

		May be there are some invalid email ids mentioned in the invoice.

		To stop sending repetitive error notifications from the system, we have unchecked
		"Convert into Recurring" field in the invoice %s.


		Please correct the invoice and make the invoice recurring again. 

		<b>It is necessary to take this action today itself for the above mentioned recurring invoice \
		to be generated. If delayed, you will have to manually change the "Repeat on Day of Month" field \
		of this invoice for generating the recurring invoice.</b>

		Regards,
		Administrator
		
	""" % (inv, website.get_site_address(), inv)
	subj = "[Urgent] Error while creating recurring invoice from %s" % inv

	from webnotes.profile import get_system_managers
	recipients = get_system_managers()
	owner_email = webnotes.conn.get_value("Profile", owner, "email")
	if not owner_email in recipients:
		recipients.append(owner_email)

	assign_task_to_owner(inv, exception_msg, recipients)
	sendmail(recipients, subject=subj, msg = exception_msg)
	
	

def assign_task_to_owner(inv, msg, users):
	for d in users:
		if d.lower() == 'administrator':
			d = webnotes.conn.sql("select ifnull(email_id, '') \
				from `tabProfile` where name = 'Administrator'")[0][0]
		from webnotes.widgets.form import assign_to
		args = {
			'assign_to' 	:	d,
			'doctype'		:	'Sales Invoice',
			'name'			:	inv,
			'description'	:	msg,
			'priority'		:	'Urgent'
		}
		assign_to.add(args)


def create_new_invoice(prev_rv):
	# clone rv
	from webnotes.model.utils import clone
	
	new_rv = clone(prev_rv)

	mdict = {'Monthly': 1, 'Quarterly': 3, 'Half-yearly': 6, 'Yearly': 12}
	mcount = mdict[prev_rv.doc.recurring_type]

	# update new rv 

	new_rv.doc.posting_date = new_rv.doc.next_date
	new_rv.doc.aging_date = new_rv.doc.next_date
	new_rv.doc.due_date = add_days(new_rv.doc.next_date, cint(date_diff(prev_rv.doc.due_date, prev_rv.doc.posting_date)))
	new_rv.doc.invoice_period_from_date = get_next_date(new_rv.doc.invoice_period_from_date, mcount)
	new_rv.doc.invoice_period_to_date = get_next_date(new_rv.doc.invoice_period_to_date, mcount)
	new_rv.doc.owner = prev_rv.doc.owner
	new_rv.doc.save()

	# submit and after submit
	new_rv.submit()
	new_rv.update_after_submit()

	return new_rv

def get_next_date(dt, mcount):
	import datetime
	m = getdate(dt).month + mcount
	y = getdate(dt).year
	d = getdate(dt).day
	if m > 12:
		m, y = m-12, y+1
	try:
		next_month_date = datetime.date(y, m, d)
	except:
		import calendar
		last_day = calendar.monthrange(y, m)[1]
		next_month_date = datetime.date(y, m, last_day)
	return next_month_date.strftime("%Y-%m-%d")


def send_notification(new_rv):
	"""Notify concerned persons about recurring invoice generation"""
	subject = "Invoice : " + new_rv.doc.name

	com = new_rv.doc.company   # webnotes.conn.get_value('Control Panel', '', 'letter_head')

	hd = '''<div><h2>%s</h2></div>
			<div><h3>Invoice: %s</h3></div>
			<table cellspacing= "5" cellpadding="5"  width = "100%%">
				<tr>
					<td width = "50%%"><b>Customer</b><br>%s<br>%s</td>
					<td width = "50%%">Invoice Date	   : %s<br>Invoice Period : %s to %s <br>Due Date	   : %s</td>
				</tr>
			</table>
		''' % (com, new_rv.doc.name, new_rv.doc.customer_name, new_rv.doc.address_display, getdate(new_rv.doc.posting_date).strftime("%d-%m-%Y"), \
		getdate(new_rv.doc.invoice_period_from_date).strftime("%d-%m-%Y"), getdate(new_rv.doc.invoice_period_to_date).strftime("%d-%m-%Y"),\
		getdate(new_rv.doc.due_date).strftime("%d-%m-%Y"))
	
	
	tbl = '''<table border="1px solid #CCC" width="100%%" cellpadding="0px" cellspacing="0px">
				<tr>
					<td width = "15%%" bgcolor="#CCC" align="left"><b>Item</b></td>
					<td width = "40%%" bgcolor="#CCC" align="left"><b>Description</b></td>
					<td width = "15%%" bgcolor="#CCC" align="center"><b>Qty</b></td>
					<td width = "15%%" bgcolor="#CCC" align="center"><b>Rate</b></td>
					<td width = "15%%" bgcolor="#CCC" align="center"><b>Amount</b></td>
				</tr>
		'''
	for d in getlist(new_rv.doclist, 'entries'):
		tbl += '<tr><td>' + d.item_code +'</td><td>' + d.description+'</td><td>' + cstr(d.qty) +'</td><td>' + cstr(d.rate) +'</td><td>' + cstr(d.amount) +'</td></tr>'
	tbl += '</table>'

	totals =''' <table cellspacing= "5" cellpadding="5"  width = "100%%">
					<tr>
						<td width = "50%%"></td>
						<td width = "50%%">
							<table width = "100%%">
								<tr>
									<td width = "50%%">Net Total: </td><td>%s </td>
								</tr><tr>
									<td width = "50%%">Total Tax: </td><td>%s </td>
								</tr><tr>
									<td width = "50%%">Grand Total: </td><td>%s</td>
								</tr><tr>
									<td width = "50%%">In Words: </td><td>%s</td>
								</tr>
							</table>
						</td>
					</tr>
					<tr><td>Terms and Conditions:</td></tr>
					<tr><td>%s</td></tr>
				</table>
			''' % (new_rv.doc.net_total, new_rv.doc.taxes_and_charges_total,new_rv.doc.grand_total, new_rv.doc.in_words,new_rv.doc.terms)


	msg = hd + tbl + totals
	recipients = new_rv.doc.notification_email_address.replace('\n', '').replace(' ', '').split(",")
	sendmail(recipients, subject=subject, msg = msg)
