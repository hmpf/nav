/* Javascript forced and smeared on the Unrecognized Neighbors page. No tests, no fuzz :p */
require(['libs/jquery', 'libs/jquery.dataTables.min'], function() {

    console.log('Neighbors script loaded');

    /* Some global variables */
    var $table = $('#unrecognized-neighbors-table'),
        $tableBody = $table.find('tbody'),
        stateActive = 'active',
        stateIgnored = 'ignored',
        setIgnoredUrl = NAV.urls.neighbors.neighbors_set_state;


    /** Set layout for all buttons based on ignored state */
    function updateButtonStates() {
        $table.find('.action-cell .table-button').each(setLayoutState);
    }


    function setButtonState($button, ignored) {
        $button.attr('data-state', ignored ? 'ignored' : 'active');
        setLayoutState($button);
    }


    /** Update button layout based on its current state */
    function setLayoutState($button) {
        $button = $button.length ? $button : $(this);
        if (isStateActive($button)) {
            $button.removeClass('secondary').text('Ignore');
        } else if (isStateIgnored($button)) {
            $button.addClass('secondary').text('Unignore');
        }
    }

    function isStateIgnored($button) {
        return $button.attr('data-state') === stateIgnored;
    }


    function isStateActive($button) {
        return $button.attr('data-state') === stateActive;
    }


    /** Add event listeners to table for manipulating neighbor ignored state */
    function addIgnoreHandlers() {
        // When ignore button is clicked, save neighbor state
        $tableBody.on('click', '.table-button', function(event) {
            var $button = $(event.target),
                $row = $(event.target).closest('tr');

            if (isStateActive($button)) {
                setIgnored($row, $button, true);
            } else if (isStateIgnored($button)) {
                setIgnored($row, $button, false);
            } else {
                console.log('No such state: ' + $button.attr('data-state'));
            }
        });

    }


    /** Set ignored state on neighbor by executing a request to controller */
    function setIgnored($row, $button, ignored) {
        console.log('setIgnored');
        var $ignoredSince = $row.find('.ignored-since');

        $button.prop('disabled', true); // Disable button to avoid spamming for requests
        $row.find('.action-cell .alert-box').remove(); // Remove any previous error messages

        var request = $.post(setIgnoredUrl, {
            neighborid: $row.data('neighborid'),
            ignored: ignored
        });

        // When request is done successfully
        request.done(function(response) {
            console.log('Request was successful');
            $ignoredSince.html(response); // Set or remove ignored timestamp
            setButtonState($button, ignored);
        });

        // In case of error give user feedback
        request.fail(function() {
            console.log('Request was not successful');
            $row.find('.action-cell').append(
                $('<span class="alert-box alert" style="display: inline">Error altering neighbor</span>')
            );
        });

        request.always(function() {
            $button.prop('disabled', false); // Re-enable button
        });

    }

    /** Apply datatables plugin to table */
    function applyDatatable() {
        return $table.dataTable({
            "bFilter": true,      // Explicitly set filtering
            "bSort": true,        // Explicitly set sorting
            "bPaginate": false,   // Do not show pagination
            "bAutoWidth": false,  // Do not calculate row width
            "sDom": 'fit',  //  Put filter and info at the top of the table
            "oLanguage": {
                "sInfo": "_TOTAL_ neighbors"  // Format for number of entries visible
            },
            "aoColumnDefs": [
                { 'bSortable': false, 'aTargets': [ -1 ] }  // Do not sort on last column
            ]
        });
    }


    /** Display the table */
    function showTable() {
        $table.show();
    }


    /* On page ready the following happens */
    $(function() {
        console.log('Neighbors ready');
        updateButtonStates();
        addIgnoreHandlers();
        applyDatatable();
        showTable();
    });


});
