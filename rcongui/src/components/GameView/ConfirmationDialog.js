import {Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle} from "@material-ui/core";

export default function ConfirmationDialog({open,onClose, onConfirm,title}) {

    return (
        <div>
            <Dialog
                open={open}
                onClose={onClose}
                aria-labelledby="alert-dialog-title"
                aria-describedby="alert-dialog-description"
            >
                <DialogTitle id="alert-dialog-title">
                    {title ? title : "Are you sure?"}
                </DialogTitle>
                {/*<DialogContent>*/}
                {/*    <DialogContentText id="alert-dialog-description">*/}
                {/*        Let Google help apps determine location. This means sending anonymous*/}
                {/*        location data to Google, even when no apps are running.*/}
                {/*    </DialogContentText>*/}
                {/*</DialogContent>*/}
                <DialogActions>
                    <Button onClick={onClose}>Cancel</Button>
                    <Button onClick={()=> {
                        onClose();
                        onConfirm();
                    } } autoFocus>
                        Confirm
                    </Button>
                </DialogActions>
            </Dialog>
        </div>
    );
}