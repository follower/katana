define(["datatables-plugin","helpers","libs/natural-sort"],function(e,t,n){var r;return r={init:function(){r.initSortNatural();var e=$(".tablesorter-js");e.each(function(e,n){var r=$(n),i={bPaginate:!1,bLengthChange:!1,bFilter:!1,bSort:!0,bInfo:!1,bAutoWidth:!1,sDom:'<"table-wrapper"t>',bRetrieve:!0,asSorting:!0,bServerSide:!1,bSearchable:!0,aaSorting:[],iDisplayLength:50,bStateSave:!1};$(this).hasClass("tools-js")&&(i.bPaginate=!0,i.bLengthChange=!0,i.bInfo=!0,i.bFilter=!0,i.oLanguage={sSearch:"",sLengthMenu:'Entries per page<select><option value="10">10</option><option value="25">25</option><option value="50">50</option><option value="100">100</option><option value="-1">All</option></select>'},i.sDom='<"top"flip><"table-wrapper"t><"bottom"pi>'),$(this).hasClass("input-js")&&(i.bFilter=!0,i.oLanguage={sSearch:""},i.sDom='<"top"flip><"table-wrapper"t><"bottom"pi>');var s=undefined,o=[],u=r.attr("data-default-sort-dir")||"asc";n.hasAttribute("data-default-sort-col")&&(s=parseInt(r.attr("data-default-sort-col")),i.aaSorting=[[s,u]]),$("> thead th",this).each(function(e,t){$(t).hasClass("no-tablesorter-js")?o.push({bSortable:!1}):s!==undefined&&s===e?o.push({sType:"natural"}):o.push(null)}),i.aoColumns=o;var a=$(this).dataTable(i),f=$(".dataTables_wrapper .top"),l=$(".dataTables_filter input");$("#builders_page").length&&window.location.search!=""&&t.codeBaseBranchOverview(f),l.attr("placeholder","Filter results").focus().keydown(function(e){a.fnFilter($(this).val())})})},initSortNatural:function(){jQuery.extend(jQuery.fn.dataTableExt.oSort,{"natural-pre":function(e){return $(e).text().trim()},"natural-asc":function(e,t){return n.sort(e,t)},"natural-desc":function(e,t){return n.sort(e,t)*-1}})}},r});