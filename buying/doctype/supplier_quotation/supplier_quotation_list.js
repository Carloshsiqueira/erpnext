// render
wn.doclistviews['Supplier Quotation'] = wn.views.ListView.extend({
	init: function(d) {
		this._super(d)
		this.fields = this.fields.concat([
			"`tabSupplier Quotation`.supplier_name",
			"`tabSupplier Quotation`.currency", 
			"ifnull(`tabSupplier Quotation`.grand_total_print,0) as grand_total_print",
			"`tabSupplier Quotation`.posting_date",
		]);
		this.stats = this.stats.concat(['status', 'company']);
	},
	
	columns: [
		{width: '3%', content: 'check'},
		{width: '5%', content:'avatar'},
		{width: '3%', content:'docstatus'},
		{width: '15%', content:'name'},
		{width: '44%', content:'supplier_name+tags', css: {color:'#222'}},
		{
			width: '18%', 
			content: function(parent, data) { 
				$(parent).html(data.currency + ' ' + fmt_money(data.grand_total_print)) 
			},
			css: {'text-align':'right'}
		},
		{width: '12%', content:'posting_date',
			css: {'text-align': 'right', 'color':'#777'},
			title: "Supplier Quotation Date", type: "date"}
	]

});

